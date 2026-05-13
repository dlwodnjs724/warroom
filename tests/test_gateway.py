"""Gateway webhook 엔드포인트 통합 테스트 (cross-package).

TestClient context manager 가 lifespan 을 실행 → init_schema() 가 in-memory
SQLite 에 테이블을 만든다. _isolate_db (root conftest) 가 매 테스트마다
fresh engine 보장.

dedupe / 카테고리 분기 / 서명 검증을 전 경로로 검증한다. 파이프라인 실행은
monkeypatch 로 대체해 외부 LLM 호출 없이 빠르게 돌린다.
"""
import hashlib
import hmac
import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MOCK_PIPELINE", "true")
    import gateway.main as main_mod

    with TestClient(main_mod.app) as c:
        yield c, main_mod


SENTRY_PAYLOAD = {
    "data": {"issue": {"id": "sentry-dupe-1", "title": "NPE", "level": "error"}},
    "action": "created",
}


class TestSentryWebhookDedupe:
    def test_first_event_is_accepted_and_runs_pipeline(self, client):
        c, main_mod = client
        with patch.object(main_mod, "_run_pipeline") as run:
            resp = c.post("/webhook/sentry", json=SENTRY_PAYLOAD)

        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"
        assert resp.json()["incident_id"] == "sentry-dupe-1"
        run.assert_called_once()

    def test_duplicate_event_returns_duplicate_without_pipeline(self, client):
        c, main_mod = client
        with patch.object(main_mod, "_run_pipeline"):
            c.post("/webhook/sentry", json=SENTRY_PAYLOAD)

        with patch.object(main_mod, "_run_pipeline") as run:
            resp = c.post("/webhook/sentry", json=SENTRY_PAYLOAD)

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "duplicate"
        assert body["dupe_count"] == 2
        run.assert_not_called()

    def test_repeated_duplicates_increment_counter(self, client):
        c, main_mod = client
        with patch.object(main_mod, "_run_pipeline"):
            c.post("/webhook/sentry", json=SENTRY_PAYLOAD)
        for expected in (2, 3, 4):
            with patch.object(main_mod, "_run_pipeline"):
                resp = c.post("/webhook/sentry", json=SENTRY_PAYLOAD)
            assert resp.json()["dupe_count"] == expected


class TestApprovalPrSkip:
    """승인 시 category 가 code 가 아니면 PR 생성을 건너뛴다."""

    async def _seed_incident(self, main_mod, category):
        from common.models import (
            IncidentCategory,
            IncidentEvent,
            IncidentStatus,
            ResolutionReport,
            Severity,
        )

        store = main_mod.get_store()
        event = IncidentEvent(
            incident_id=f"INC-{category}-1",
            source="sentry",
            title="test",
            raw_payload={},
        )
        await store.add(event)
        report = ResolutionReport(
            incident_id=event.incident_id,
            severity=Severity.HIGH,
            category=IncidentCategory(category),
            triage_summary="t",
            root_cause="r",
            patch_suggestion="p" if category == "code" else "",
            post_mortem_draft="pm",
        )
        await store.save_report(event.incident_id, report)
        await store.update_status(event.incident_id, IncidentStatus.AWAITING_APPROVAL)
        return event.incident_id

    async def test_code_category_attempts_pr(self, client, monkeypatch):
        c, main_mod = client
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = await self._seed_incident(main_mod, "code")

        resp = c.post(f"/incidents/{incident_id}/approve")
        assert resp.status_code == 200
        body = resp.json()
        assert "pull_request" in body
        assert body["pull_request"]["dry_run"] is True

    async def test_infra_category_skips_pr(self, client, monkeypatch):
        c, main_mod = client
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = await self._seed_incident(main_mod, "infra")

        resp = c.post(f"/incidents/{incident_id}/approve")
        body = resp.json()
        assert body["pull_request"]["skipped"] is True
        assert "infra" in body["pull_request"]["reason"]

    async def test_external_category_skips_pr(self, client, monkeypatch):
        c, main_mod = client
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = await self._seed_incident(main_mod, "external")

        resp = c.post(f"/incidents/{incident_id}/approve")
        assert resp.json()["pull_request"]["skipped"] is True


class TestWebhookSignatureVerification:
    def test_sentry_rejects_invalid_signature(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "topsecret")
        resp = c.post(
            "/webhook/sentry",
            json=SENTRY_PAYLOAD,
            headers={"X-Sentry-Signature": "wrong-sig"},
        )
        assert resp.status_code == 401

    def test_sentry_rejects_missing_signature(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "topsecret")
        resp = c.post("/webhook/sentry", json=SENTRY_PAYLOAD)
        assert resp.status_code == 401

    def test_sentry_accepts_valid_signature(self, client, monkeypatch):
        c, main_mod = client
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "topsecret")
        body = json.dumps(SENTRY_PAYLOAD).encode("utf-8")
        sig = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()

        with patch.object(main_mod, "_run_pipeline"):
            resp = c.post(
                "/webhook/sentry",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Sentry-Signature": sig,
                },
            )
        assert resp.status_code == 202

    def test_datadog_rejects_wrong_token(self, client, monkeypatch):
        c, _ = client
        monkeypatch.setenv("WARROOM_DATADOG_TOKEN", "shared-xyz")
        resp = c.post(
            "/webhook/datadog",
            json={"id": "x", "title": "t"},
            headers={"X-Warroom-Token": "nope"},
        )
        assert resp.status_code == 401

    def test_datadog_accepts_valid_token(self, client, monkeypatch):
        c, main_mod = client
        monkeypatch.setenv("WARROOM_DATADOG_TOKEN", "shared-xyz")
        with patch.object(main_mod, "_run_pipeline"):
            resp = c.post(
                "/webhook/datadog",
                json={"id": "dd-sig-1", "title": "t"},
                headers={"X-Warroom-Token": "shared-xyz"},
            )
        assert resp.status_code == 202


class TestDatadogWebhookDedupe:
    DD_PAYLOAD = {"id": "dd-mon-1", "title": "CPU high", "alert_type": "error"}

    def test_first_datadog_event_accepted(self, client):
        c, main_mod = client
        with patch.object(main_mod, "_run_pipeline") as run:
            resp = c.post("/webhook/datadog", json=self.DD_PAYLOAD)
        assert resp.json()["status"] == "accepted"
        run.assert_called_once()

    def test_datadog_duplicate_skipped(self, client):
        c, main_mod = client
        with patch.object(main_mod, "_run_pipeline"):
            c.post("/webhook/datadog", json=self.DD_PAYLOAD)
        with patch.object(main_mod, "_run_pipeline") as run:
            resp = c.post("/webhook/datadog", json=self.DD_PAYLOAD)
        assert resp.json()["status"] == "duplicate"
        run.assert_not_called()
