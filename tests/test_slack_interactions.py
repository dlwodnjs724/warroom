"""Slack Interactivity endpoint 통합 테스트.

/slack/interactions 한 endpoint 가 받는 두 payload (block_actions /
view_submission) 와 서명 검증 / approve / reject / modal open / 사유 영속화
까지 전 경로 검증.

handle_decision 의 PR 생성/cleanup 은 dry-run GitHub client 가 처리 (이미
다른 테스트에서 검증), 본 테스트는 Slack 진입점 ↔ services.decisions 의 wiring 만.
"""

import hashlib
import hmac
import json
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

# Slack 서명 replay 윈도우 (±5분) 검사의 결정성 확보를 위해 단일 시각 고정.
_FROZEN_EPOCH = 1_700_000_000


@pytest.fixture(autouse=True)
def _freeze_clock(monkeypatch):
    """``common.clock.now`` 를 고정 — verify_slack_signature 의 replay 검사가 매번 같은 결과."""
    frozen = datetime.fromtimestamp(_FROZEN_EPOCH, tz=ZoneInfo("UTC"))
    monkeypatch.setattr("gateway.infrastructure.monitors.security._now", lambda: frozen)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MOCK_PIPELINE", "true")
    import gateway.main as main_mod

    with TestClient(main_mod.app) as c:
        yield c


def _slack_sign(secret: str, body: bytes, ts: str) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


def _send(client, body: bytes, *, secret: str | None = None, ts: str | None = None):
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if secret:
        ts = ts or str(_FROZEN_EPOCH)
        headers["X-Slack-Signature"] = _slack_sign(secret, body, ts)
        headers["X-Slack-Request-Timestamp"] = ts
    return client.post("/slack/interactions", content=body, headers=headers)


async def _seed_awaiting(incident_id: str = "INC-SLACK-1") -> str:
    from common.models import (
        IncidentCategory,
        IncidentEvent,
        IncidentStatus,
        ResolutionReport,
        Severity,
    )
    from gateway.infrastructure.db.repository import get_repository

    repo = get_repository()
    await repo.add(IncidentEvent(incident_id=incident_id, source="sentry", title="t", raw_payload={}))
    await repo.save_report(
        incident_id,
        ResolutionReport(
            incident_id=incident_id,
            severity=Severity.HIGH,
            category=IncidentCategory.CODE,
            triage_summary="t",
            root_cause="r",
            patch_suggestion="p",
            post_mortem_draft="pm",
        ),
    )
    await repo.update_status(incident_id, IncidentStatus.AWAITING_APPROVAL)
    return incident_id


def _form_body(payload: dict) -> bytes:
    return urlencode({"payload": json.dumps(payload)}).encode("utf-8")


class TestSignatureVerification:
    """SLACK_SIGNING_SECRET 이 설정된 환경에서 위변조 / 헤더 누락 거부."""

    SECRET = "test-signing-secret"

    def test_rejects_missing_signature_when_secret_set(self, client, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self.SECRET)
        body = _form_body({"type": "block_actions", "actions": []})
        resp = client.post("/slack/interactions", content=body)
        assert resp.status_code == 401

    def test_rejects_tampered_body(self, client, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self.SECRET)

        ts = str(_FROZEN_EPOCH)
        original = _form_body({"type": "block_actions", "actions": []})
        sig = _slack_sign(self.SECRET, original, ts)
        # 서명은 original 기반, body 는 다른 값
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Slack-Signature": sig,
            "X-Slack-Request-Timestamp": ts,
        }
        resp = client.post("/slack/interactions", content=b"payload=tampered", headers=headers)
        assert resp.status_code == 401

    def test_accepts_valid_signature(self, client, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self.SECRET)
        body = _form_body({"type": "block_actions", "actions": []})
        resp = _send(client, body, secret=self.SECRET)
        # block_actions with empty actions → 200 (no-op)
        assert resp.status_code == 200

    def test_skips_verification_when_secret_missing(self, client, monkeypatch):
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        body = _form_body({"type": "block_actions", "actions": []})
        resp = client.post(
            "/slack/interactions",
            content=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 200


class TestBadRequests:
    def test_missing_payload_field(self, client):
        resp = client.post(
            "/slack/interactions",
            content=b"foo=bar",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 400

    def test_invalid_payload_json(self, client):
        resp = client.post(
            "/slack/interactions",
            content=b"payload=not-json",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 400

    def test_unknown_payload_type_ignored(self, client):
        body = _form_body({"type": "shortcut"})
        resp = _send(client, body)
        assert resp.status_code == 200


class TestApproveButton:
    async def test_approve_action_routes_to_handle_decision(self, client, monkeypatch):
        """warroom_approve 클릭 → background task 가 handle_decision(approved=True)."""
        from common.models import IncidentStatus
        from gateway.infrastructure.db.repository import get_repository

        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = await _seed_awaiting("INC-SLACK-APPROVE")

        body = _form_body(
            {
                "type": "block_actions",
                "trigger_id": "trig-1",
                "actions": [{"action_id": "warroom_approve", "value": incident_id}],
            }
        )
        resp = _send(client, body)
        assert resp.status_code == 200

        # BackgroundTasks 는 TestClient 의 response 직후 flush 됨 → DB 상태 검증.
        repo = get_repository()
        entry = await repo.get(incident_id)
        assert entry is not None
        assert entry["status"] == IncidentStatus.APPROVED

    async def test_approve_for_missing_incident_does_not_raise(self, client):
        """존재하지 않는 incident_id — 백그라운드 task 가 HTTPException 흡수 (log 만)."""
        body = _form_body(
            {
                "type": "block_actions",
                "trigger_id": "trig-1",
                "actions": [{"action_id": "warroom_approve", "value": "INC-NONEXIST"}],
            }
        )
        resp = _send(client, body)
        # endpoint 자체는 200 유지 — 슬랙에 4xx 보이면 안 됨.
        assert resp.status_code == 200


class TestRejectButton:
    async def test_reject_action_opens_modal(self, client, monkeypatch):
        """warroom_reject 클릭 → SlackNotifier.open_reject_modal 호출 (DB 상태 변경 없음)."""
        from common.models import IncidentStatus
        from gateway.dependencies import get_slack_notifier
        from gateway.infrastructure.db.repository import get_repository

        incident_id = await _seed_awaiting("INC-SLACK-REJECT-BTN")
        calls: list[tuple[str, str]] = []
        notifier = get_slack_notifier()
        monkeypatch.setattr(
            notifier,
            "open_reject_modal",
            lambda trigger_id, iid: calls.append((trigger_id, iid)),
        )

        body = _form_body(
            {
                "type": "block_actions",
                "trigger_id": "trig-xyz",
                "actions": [{"action_id": "warroom_reject", "value": incident_id}],
            }
        )
        resp = _send(client, body)
        assert resp.status_code == 200
        assert calls == [("trig-xyz", incident_id)]

        # 버튼 클릭만으로는 상태 변경 안 됨 — modal submit 후 변경.
        repo = get_repository()
        entry = await repo.get(incident_id)
        assert entry is not None
        assert entry["status"] == IncidentStatus.AWAITING_APPROVAL

    async def test_reject_action_missing_trigger_id_silent(self, client, monkeypatch):
        from gateway.dependencies import get_slack_notifier

        incident_id = await _seed_awaiting("INC-SLACK-REJECT-NOTRIG")
        calls: list = []
        notifier = get_slack_notifier()
        monkeypatch.setattr(notifier, "open_reject_modal", lambda *a, **kw: calls.append(a))

        body = _form_body(
            {
                "type": "block_actions",
                # trigger_id 누락
                "actions": [{"action_id": "warroom_reject", "value": incident_id}],
            }
        )
        resp = _send(client, body)
        assert resp.status_code == 200
        assert calls == []  # modal 시도 안 함

    async def test_unknown_action_id_ignored(self, client):
        body = _form_body(
            {
                "type": "block_actions",
                "trigger_id": "trig-1",
                "actions": [{"action_id": "warroom_unknown", "value": "INC-X"}],
            }
        )
        resp = _send(client, body)
        assert resp.status_code == 200


class TestRejectModalSubmit:
    async def test_view_submission_persists_reason_and_rejects(self, client):
        """view_submission → handle_decision(approved=False, rejection_reason=...) 호출 + 영속화."""
        from common.models import IncidentStatus
        from gateway.infrastructure.db.repository import get_repository

        incident_id = await _seed_awaiting("INC-SLACK-MODAL-1")

        body = _form_body(
            {
                "type": "view_submission",
                "view": {
                    "callback_id": "warroom_reject_modal",
                    "private_metadata": incident_id,
                    "state": {
                        "values": {
                            "reason_block": {
                                "reason": {"value": "롤백 비용이 더 큼"},
                            }
                        }
                    },
                },
            }
        )
        resp = _send(client, body)
        assert resp.status_code == 200

        repo = get_repository()
        entry = await repo.get(incident_id)
        assert entry is not None
        assert entry["status"] == IncidentStatus.REJECTED
        assert entry["rejection_reason"] == "롤백 비용이 더 큼"

    async def test_view_submission_empty_reason_still_rejects(self, client):
        """사유 비어도 reject 자체는 진행 (modal 의 input 이 optional 인 경우 대비)."""
        from common.models import IncidentStatus
        from gateway.infrastructure.db.repository import get_repository

        incident_id = await _seed_awaiting("INC-SLACK-MODAL-EMPTY")
        body = _form_body(
            {
                "type": "view_submission",
                "view": {
                    "callback_id": "warroom_reject_modal",
                    "private_metadata": incident_id,
                    "state": {"values": {}},
                },
            }
        )
        resp = _send(client, body)
        assert resp.status_code == 200

        repo = get_repository()
        entry = await repo.get(incident_id)
        assert entry is not None
        assert entry["status"] == IncidentStatus.REJECTED
        assert entry["rejection_reason"] is None

    async def test_unknown_view_callback_ignored(self, client):
        body = _form_body(
            {
                "type": "view_submission",
                "view": {"callback_id": "some_other_modal", "private_metadata": "X"},
            }
        )
        resp = _send(client, body)
        assert resp.status_code == 200
