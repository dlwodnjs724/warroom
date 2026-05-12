"""SlackNotifier 단위 테스트."""
import json
from datetime import datetime

import pytest

from chatops.slack import SlackNotifier, _truncate
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
        created_at=datetime(2026, 5, 7, 10, 0, 0),
    )


class TestDryRun:
    def test_dry_run_when_url_missing(self, event, tmp_path):
        log = tmp_path / "slack.jsonl"
        notifier = SlackNotifier(webhook_url=None, dry_run_log=str(log))
        notifier.on_incident_received(event)

        assert log.exists()
        lines = log.read_text().splitlines()
        assert len(lines) == 1
        payload = json.loads(lines[0])
        assert payload["blocks"][0]["type"] == "header"
        assert "sentry-test" in payload["blocks"][0]["text"]["text"]

    def test_dry_run_appends_each_event(self, event, report, tmp_path):
        log = tmp_path / "slack.jsonl"
        notifier = SlackNotifier(webhook_url=None, dry_run_log=str(log))
        notifier.on_incident_received(event)
        notifier.on_resolution_ready(report)

        lines = log.read_text().splitlines()
        assert len(lines) == 2

    def test_agent_update_does_not_log(self, tmp_path):
        log = tmp_path / "slack.jsonl"
        notifier = SlackNotifier(webhook_url=None, dry_run_log=str(log))
        notifier.on_agent_update("Triage Agent", "분류 중...")
        # 진행 업데이트는 Slack 도배 방지를 위해 무시
        assert not log.exists()


class TestMessageStructure:
    def test_incident_message_has_source_and_title(self, event, tmp_path):
        log = tmp_path / "slack.jsonl"
        notifier = SlackNotifier(webhook_url=None, dry_run_log=str(log))
        notifier.on_incident_received(event)
        payload = json.loads(log.read_text().splitlines()[0])

        fields_text = json.dumps(payload, ensure_ascii=False)
        assert "SENTRY" in fields_text
        assert "NullPointerException" in fields_text

    def test_resolution_message_includes_severity_emoji(self, report, tmp_path):
        log = tmp_path / "slack.jsonl"
        notifier = SlackNotifier(webhook_url=None, dry_run_log=str(log))
        notifier.on_resolution_ready(report)
        payload = json.loads(log.read_text().splitlines()[0])

        header_text = payload["blocks"][0]["text"]["text"]
        assert "⚠️" in header_text  # HIGH 이모지
        assert "sentry-test" in header_text

    def test_resolution_message_has_approve_reject_buttons(self, report, tmp_path):
        log = tmp_path / "slack.jsonl"
        notifier = SlackNotifier(webhook_url=None, dry_run_log=str(log))
        notifier.on_resolution_ready(report)
        payload = json.loads(log.read_text().splitlines()[0])

        actions = next(b for b in payload["blocks"] if b["type"] == "actions")
        action_ids = [el["action_id"] for el in actions["elements"]]
        assert "warroom_approve" in action_ids
        assert "warroom_reject" in action_ids
        # 두 버튼 모두 incident_id를 value로 가짐
        assert all(el["value"] == "sentry-test" for el in actions["elements"])


class TestTruncation:
    def test_truncate_short_text_unchanged(self):
        assert _truncate("hello", 10) == "hello"

    def test_truncate_exact_length_unchanged(self):
        assert _truncate("x" * 10, 10) == "x" * 10

    def test_truncate_long_text_appends_ellipsis(self):
        result = _truncate("x" * 100, 10)
        assert len(result) == 10
        assert result.endswith("…")

    def test_long_root_cause_truncated_in_message(self, tmp_path):
        long_rca = "원인" * 1000  # 4000 chars
        report = ResolutionReport(
            incident_id="x",
            severity=Severity.HIGH,
            triage_summary="",
            root_cause=long_rca,
            patch_suggestion="",
            post_mortem_draft="",
        )
        log = tmp_path / "slack.jsonl"
        notifier = SlackNotifier(webhook_url=None, dry_run_log=str(log))
        notifier.on_resolution_ready(report)
        payload = json.loads(log.read_text().splitlines()[0])

        rca_block = next(
            b for b in payload["blocks"]
            if b.get("type") == "section" and "근본 원인" in b.get("text", {}).get("text", "")
        )
        assert "…" in rca_block["text"]["text"]


class TestRealSendMode:
    def test_uses_http_client_when_url_present(self, event):
        sent = []

        class FakeClient:
            def post(self, url, json, timeout):
                sent.append((url, json))

                class Resp:
                    def raise_for_status(self):
                        pass

                return Resp()

        notifier = SlackNotifier(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            http_client=FakeClient(),
        )
        notifier.on_incident_received(event)
        assert len(sent) == 1
        url, payload = sent[0]
        assert url.startswith("https://hooks.slack.com")
        assert payload["blocks"][0]["type"] == "header"

    def test_falls_back_to_dry_run_on_http_failure(self, event, tmp_path):
        log = tmp_path / "slack.jsonl"

        class FailingClient:
            def post(self, url, json, timeout):
                raise RuntimeError("network down")

        notifier = SlackNotifier(
            webhook_url="https://hooks.slack.com/services/T/B/X",
            dry_run_log=str(log),
            http_client=FailingClient(),
        )
        notifier.on_incident_received(event)
        # 실패 시 dry-run 로그로 폴백
        assert log.exists()
        assert len(log.read_text().splitlines()) == 1
