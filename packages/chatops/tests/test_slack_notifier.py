"""SlackNotifier 단위 테스트 — Bot Token 기반 chat.postMessage / thread / chat.update.

dry-run / 실 Slack 모드 / thread 캐시 / 영속화 콜백 / 실패 폴백을 모두 검증.
"""

import json
from datetime import datetime

import pytest
from chatops.clients.slack import SlackNotifier, _truncate
from common.clock import APP_TZ
from common.models import IncidentEvent, ResolutionReport, Severity


@pytest.fixture
def event():
    return IncidentEvent(
        incident_id="sentry-test",
        source="sentry",
        title="NullPointerException at app/gateways/stripe.py",
        raw_payload={},
    )


@pytest.fixture
def report():
    return ResolutionReport(
        incident_id="sentry-test",
        severity=Severity.HIGH,
        triage_summary="HIGH severity 결제 장애",
        root_cause="charge() 메서드에서 _ensure_client() 호출 누락",
        patch_suggestion="def charge(self, ...):\n    self._ensure_client()\n    ...",
        post_mortem_draft="단/중/장기 액션 매트릭스",
        created_at=datetime(2026, 5, 7, 10, 0, 0, tzinfo=APP_TZ),
    )


class _FakeSlack:
    """chat.postMessage / chat.update 응답을 시뮬레이션."""

    def __init__(self, channel_id: str = "C123", responses: list[dict] | None = None):
        self.calls: list[tuple[str, dict, dict]] = []  # (url, headers, payload)
        self._channel_id = channel_id
        self._ts_counter = 0
        self._responses = responses or []

    def post(self, url, headers, json, timeout):
        self.calls.append((url, headers, json))
        if self._responses:
            resp_data = self._responses.pop(0)
        elif "chat.postMessage" in url:
            self._ts_counter += 1
            resp_data = {"ok": True, "channel": self._channel_id, "ts": f"1700000000.{self._ts_counter:06d}"}
        else:  # chat.update
            resp_data = {"ok": True, "channel": json["channel"], "ts": json["ts"]}

        class Resp:
            def json(self):
                return resp_data

        return Resp()


class TestDryRun:
    def test_dry_run_when_token_missing(self, event, tmp_path):
        log = tmp_path / "slack.jsonl"
        notifier = SlackNotifier(bot_token=None, dry_run_log=str(log))
        notifier.on_incident_received(event)

        assert log.exists()
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["_api"] == "chat.postMessage"
        assert rows[0]["blocks"][0]["type"] == "header"

    def test_dry_run_full_flow(self, event, report, tmp_path):
        log = tmp_path / "slack.jsonl"
        notifier = SlackNotifier(bot_token=None, dry_run_log=str(log))
        notifier.on_incident_received(event)
        notifier.on_agent_update(event.incident_id, "Triage", "분류 중")
        notifier.on_resolution_ready(report)

        rows = [json.loads(line) for line in log.read_text().splitlines()]
        # dry-run 에서는 incident_received 후 ts 캐시 미생성 → agent_update 는 skip 로그,
        # resolution 은 thread 못 찾아 새 메시지로 fallback.
        apis = [r.get("_api") or r.get("_type") for r in rows]
        assert "chat.postMessage" in apis  # incident
        assert "thread_update_skipped" in apis
        assert apis[-1] == "chat.postMessage"  # resolution fallback


class TestRealMode:
    def test_incident_then_thread_then_update(self, event, report):
        fake = _FakeSlack(channel_id="C123")
        notifier = SlackNotifier(bot_token="xoxb-test", channel="#warroom", http_client=fake)

        notifier.on_incident_received(event)
        notifier.on_agent_update(event.incident_id, "Triage", "분류 중")
        notifier.on_resolution_ready(report)

        # 3개 API 호출: postMessage(incident), postMessage(thread reply), chat.update
        assert len(fake.calls) == 3
        urls = [c[0] for c in fake.calls]
        assert urls[0].endswith("/chat.postMessage")
        assert urls[1].endswith("/chat.postMessage")
        assert urls[2].endswith("/chat.update")

        # 2번째 호출은 thread_ts 가 1번째의 ts 와 일치해야 함
        first_ts = fake.calls[0][2].get("ts") or "1700000000.000001"
        assert fake.calls[1][2]["thread_ts"] == "1700000000.000001"
        # 3번째 호출 (chat.update) 는 첫 번째 ts 를 갱신
        assert fake.calls[2][2]["ts"] == "1700000000.000001"
        del first_ts  # silence unused

    def test_auth_header_is_bearer(self, event):
        fake = _FakeSlack()
        notifier = SlackNotifier(bot_token="xoxb-secret", channel="#x", http_client=fake)
        notifier.on_incident_received(event)
        headers = fake.calls[0][1]
        assert headers["Authorization"] == "Bearer xoxb-secret"

    def test_pipeline_failed_replies_to_thread(self, event):
        fake = _FakeSlack(channel_id="C123")
        notifier = SlackNotifier(bot_token="xoxb-test", channel="#x", http_client=fake)
        notifier.on_incident_received(event)
        notifier.on_pipeline_failed(event.incident_id, "OOM killed")

        assert len(fake.calls) == 2
        thread_call = fake.calls[1][2]
        assert thread_call["thread_ts"]  # threaded
        assert "OOM killed" in thread_call["text"]


class TestPersistCallback:
    def test_persist_called_on_incident_post(self, event):
        fake = _FakeSlack(channel_id="C999")
        recorded: list[tuple[str, str, str]] = []
        notifier = SlackNotifier(
            bot_token="xoxb-test",
            channel="#x",
            http_client=fake,
            on_thread_persist=lambda iid, ch, ts: recorded.append((iid, ch, ts)),
        )
        notifier.on_incident_received(event)
        assert len(recorded) == 1
        iid, ch, ts = recorded[0]
        assert iid == event.incident_id
        assert ch == "C999"
        assert ts.startswith("1700000000.")

    def test_lookup_used_on_cache_miss(self, event, report):
        fake = _FakeSlack(channel_id="C123")
        # incident_received 를 건너뛰고 바로 resolution 만 호출 → 캐시 miss
        notifier = SlackNotifier(
            bot_token="xoxb-test",
            channel="#x",
            http_client=fake,
            thread_lookup=lambda iid: ("C123", "1700000000.999"),
        )
        notifier.on_resolution_ready(report)

        # chat.update 로 호출됐어야 함 (lookup 으로 thread 복구)
        assert len(fake.calls) == 1
        assert fake.calls[0][0].endswith("/chat.update")
        assert fake.calls[0][2]["ts"] == "1700000000.999"


class TestFailureFallback:
    def test_slack_api_error_falls_back_to_dry_run(self, event, tmp_path):
        log = tmp_path / "slack.jsonl"
        fake = _FakeSlack(responses=[{"ok": False, "error": "channel_not_found"}])
        notifier = SlackNotifier(
            bot_token="xoxb-test",
            channel="#missing",
            dry_run_log=str(log),
            http_client=fake,
        )
        notifier.on_incident_received(event)
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["_error"] == "channel_not_found"

    def test_http_exception_falls_back_to_dry_run(self, event, tmp_path):
        log = tmp_path / "slack.jsonl"

        class FailingClient:
            def post(self, url, headers, json, timeout):
                raise RuntimeError("network down")

        notifier = SlackNotifier(
            bot_token="xoxb-test",
            channel="#x",
            dry_run_log=str(log),
            http_client=FailingClient(),
        )
        notifier.on_incident_received(event)
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert len(rows) == 1
        assert "network down" in rows[0]["_exception"]


class TestMessageStructure:
    def test_resolution_blocks_include_severity_and_category(self, report):
        fake = _FakeSlack()
        notifier = SlackNotifier(bot_token="xoxb-test", channel="#x", http_client=fake)
        notifier.on_incident_received(
            IncidentEvent(incident_id=report.incident_id, source="sentry", title="t", raw_payload={})
        )
        notifier.on_resolution_ready(report)
        update_payload = fake.calls[-1][2]
        blocks_text = json.dumps(update_payload["blocks"], ensure_ascii=False)
        assert "HIGH" in blocks_text
        assert "code" in blocks_text  # category 기본값
        assert "⚠️" in blocks_text

    def test_resolution_has_approve_reject_buttons(self, report):
        fake = _FakeSlack()
        notifier = SlackNotifier(bot_token="xoxb-test", channel="#x", http_client=fake)
        notifier.on_incident_received(
            IncidentEvent(incident_id=report.incident_id, source="sentry", title="t", raw_payload={})
        )
        notifier.on_resolution_ready(report)
        blocks = fake.calls[-1][2]["blocks"]
        actions = next(b for b in blocks if b["type"] == "actions")
        action_ids = [el["action_id"] for el in actions["elements"]]
        assert "warroom_approve" in action_ids
        assert "warroom_reject" in action_ids


class TestRejectModal:
    def test_dry_run_logs_views_open_payload(self, tmp_path):
        log = tmp_path / "slack.jsonl"
        notifier = SlackNotifier(bot_token=None, dry_run_log=str(log))
        notifier.open_reject_modal("trig-123", "INC-42")

        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["_api"] == "views.open"
        assert rows[0]["trigger_id"] == "trig-123"
        view = rows[0]["view"]
        assert view["callback_id"] == "warroom_reject_modal"
        assert view["private_metadata"] == "INC-42"
        # plain_text_input block 이 incident_id 라벨에 포함되어 있어야 함
        block = view["blocks"][0]
        assert block["block_id"] == "reason_block"
        assert block["element"]["action_id"] == "reason"
        assert "INC-42" in block["label"]["text"]

    def test_real_mode_posts_views_open_with_bearer(self):
        fake = _FakeSlack(responses=[{"ok": True, "view": {"id": "V1"}}])
        notifier = SlackNotifier(bot_token="xoxb-test", channel="#x", http_client=fake)
        notifier.open_reject_modal("trig-abc", "INC-XYZ")

        assert len(fake.calls) == 1
        url, headers, payload = fake.calls[0]
        assert url.endswith("/views.open")
        assert headers["Authorization"] == "Bearer xoxb-test"
        assert payload["trigger_id"] == "trig-abc"
        assert payload["view"]["private_metadata"] == "INC-XYZ"

    def test_real_mode_falls_back_to_dry_run_on_error(self, tmp_path):
        log = tmp_path / "slack.jsonl"
        fake = _FakeSlack(responses=[{"ok": False, "error": "trigger_expired"}])
        notifier = SlackNotifier(
            bot_token="xoxb-test",
            channel="#x",
            dry_run_log=str(log),
            http_client=fake,
        )
        notifier.open_reject_modal("trig-stale", "INC-1")

        rows = [json.loads(line) for line in log.read_text().splitlines()]
        assert rows[0]["_error"] == "trigger_expired"
        assert rows[0]["_api"] == "views.open"
        # fallback 페이로드에 view 메타데이터가 보존되어야 — regression 시
        # 어떤 incident 가 modal 시도했는지 추적 불가해진다.
        assert rows[0]["view"]["private_metadata"] == "INC-1"
        assert rows[0]["trigger_id"] == "trig-stale"


class TestTruncation:
    def test_short_text_unchanged(self):
        assert _truncate("hello", 10) == "hello"

    def test_exact_length_unchanged(self):
        assert _truncate("x" * 10, 10) == "x" * 10

    def test_long_text_appends_ellipsis(self):
        result = _truncate("x" * 100, 10)
        assert len(result) == 10
        assert result.endswith("…")

    def test_long_rca_truncated_in_resolution(self, tmp_path):
        long_rca = "원인" * 2000  # 8000 chars
        rep = ResolutionReport(
            incident_id="x",
            severity=Severity.HIGH,
            triage_summary="",
            root_cause=long_rca,
            patch_suggestion="",
            post_mortem_draft="",
        )
        fake = _FakeSlack()
        notifier = SlackNotifier(bot_token="xoxb-test", channel="#x", http_client=fake)
        notifier.on_incident_received(
            IncidentEvent(incident_id="x", source="sentry", title="t", raw_payload={})
        )
        notifier.on_resolution_ready(rep)
        # chat.update 의 blocks 에서 잘린 RCA 검증
        update_call = next(c for c in fake.calls if c[0].endswith("/chat.update"))
        blocks = update_call[2]["blocks"]
        rca_block = next(
            b
            for b in blocks
            if b.get("type") == "section" and "근본 원인" in b.get("text", {}).get("text", "")
        )
        assert "…" in rca_block["text"]["text"]


class TestThreadFullTextSplit:
    """Phase 2.8 — section 한도 초과 시 thread 에 풀텍스트 reply."""

    def test_short_rca_no_thread_reply(self, event):
        """RCA 가 한도 이하면 thread reply 안 만든다."""
        rep = ResolutionReport(
            incident_id=event.incident_id,
            severity=Severity.HIGH,
            triage_summary="",
            root_cause="짧은 원인",  # cap 이하
            patch_suggestion="",
            post_mortem_draft="",
        )
        fake = _FakeSlack()
        notifier = SlackNotifier(bot_token="xoxb-test", channel="#x", http_client=fake)
        notifier.on_incident_received(event)
        notifier.on_resolution_ready(rep)

        # 2 호출만 — incident postMessage + chat.update. thread reply 없음.
        assert len(fake.calls) == 2
        assert fake.calls[0][0].endswith("/chat.postMessage")
        assert fake.calls[1][0].endswith("/chat.update")

    def test_long_rca_emits_thread_full_text(self, event):
        """RCA 가 한도 초과 → chat.update + thread postMessage(풀텍스트) 송신."""
        long_rca = "원인" * 2000  # 8000 chars, 2500 cap 초과
        rep = ResolutionReport(
            incident_id=event.incident_id,
            severity=Severity.HIGH,
            triage_summary="",
            root_cause=long_rca,
            patch_suggestion="",
            post_mortem_draft="",
        )
        fake = _FakeSlack()
        notifier = SlackNotifier(bot_token="xoxb-test", channel="#x", http_client=fake)
        notifier.on_incident_received(event)
        notifier.on_resolution_ready(rep)

        # 3 호출: incident postMessage + chat.update + thread postMessage(RCA full)
        assert len(fake.calls) == 3
        thread_call = fake.calls[2]
        assert thread_call[0].endswith("/chat.postMessage")
        payload = thread_call[2]
        assert payload.get("thread_ts"), "thread reply 여야 함"
        assert "근본 원인 (풀 텍스트)" in payload["text"]
        # 풀텍스트 전체가 들어감 (truncate 없음)
        assert long_rca in payload["text"]

    def test_long_patch_emits_thread_full_text_after_redact(self, event):
        """patch 풀텍스트는 redaction 거친 뒤 thread 로."""
        long_patch = "diff line\n" * 400  # ~3600 chars, cap 초과
        leaked = "sk-" + "a" * 48  # OpenAI 패턴
        long_patch_with_secret = leaked + "\n" + long_patch
        rep = ResolutionReport(
            incident_id=event.incident_id,
            severity=Severity.HIGH,
            triage_summary="",
            root_cause="",
            patch_suggestion=long_patch_with_secret,
            post_mortem_draft="",
        )
        fake = _FakeSlack()
        notifier = SlackNotifier(bot_token="xoxb-test", channel="#x", http_client=fake)
        notifier.on_incident_received(event)
        notifier.on_resolution_ready(rep)

        thread_calls = [c for c in fake.calls if c[2].get("thread_ts")]
        assert len(thread_calls) == 1
        body = thread_calls[0][2]["text"]
        assert "패치 제안 (풀 텍스트)" in body
        assert leaked not in body, "secret 패턴 redaction 통과해야 함"
        assert "[REDACTED:openai_api_key]" in body

    def test_long_rca_and_patch_emit_two_thread_replies(self, event):
        long_rca = "원인" * 2000
        long_patch = "patch line\n" * 400
        rep = ResolutionReport(
            incident_id=event.incident_id,
            severity=Severity.HIGH,
            triage_summary="",
            root_cause=long_rca,
            patch_suggestion=long_patch,
            post_mortem_draft="",
        )
        fake = _FakeSlack()
        notifier = SlackNotifier(bot_token="xoxb-test", channel="#x", http_client=fake)
        notifier.on_incident_received(event)
        notifier.on_resolution_ready(rep)

        thread_calls = [c for c in fake.calls if c[2].get("thread_ts")]
        assert len(thread_calls) == 2
        labels = [c[2]["text"][:30] for c in thread_calls]
        assert any("근본 원인" in lbl for lbl in labels)
        assert any("패치 제안" in lbl for lbl in labels)
