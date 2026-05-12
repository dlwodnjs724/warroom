"""Gateway webhook 엔드포인트 테스트.

dedupe 분기와 BackgroundTask 호출 여부를 fastapi.TestClient 로 검증한다.
파이프라인 실행 자체는 monkeypatch 로 대체해 외부 LLM 호출을 피한다.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    # 모든 테스트가 격리된 in-memory store 를 쓰도록 강제
    monkeypatch.setenv("WARROOM_STORE", "memory")
    monkeypatch.setenv("MOCK_PIPELINE", "true")
    # 모듈 캐시 무효화 후 fresh import
    import importlib
    import gateway.store as store_mod
    import gateway.main as main_mod
    importlib.reload(store_mod)
    importlib.reload(main_mod)
    return TestClient(main_mod.app), main_mod, store_mod


SENTRY_PAYLOAD = {
    "data": {"issue": {"id": "sentry-dupe-1", "title": "NPE", "level": "error"}},
    "action": "created",
}


class TestSentryWebhookDedupe:
    def test_first_event_is_accepted_and_runs_pipeline(self, client):
        c, main_mod, _ = client
        with patch.object(main_mod, "_run_pipeline") as run:
            resp = c.post("/webhook/sentry", json=SENTRY_PAYLOAD)

        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"
        assert resp.json()["incident_id"] == "sentry-dupe-1"
        run.assert_called_once()

    def test_duplicate_event_returns_duplicate_without_pipeline(self, client):
        c, main_mod, store_mod = client
        # 첫 등록
        with patch.object(main_mod, "_run_pipeline"):
            c.post("/webhook/sentry", json=SENTRY_PAYLOAD)

        # 두 번째는 중복으로 처리되어야 함
        with patch.object(main_mod, "_run_pipeline") as run:
            resp = c.post("/webhook/sentry", json=SENTRY_PAYLOAD)

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "duplicate"
        assert body["dupe_count"] == 2
        run.assert_not_called()

    def test_repeated_duplicates_increment_counter(self, client):
        c, main_mod, _ = client
        with patch.object(main_mod, "_run_pipeline"):
            c.post("/webhook/sentry", json=SENTRY_PAYLOAD)
        for expected in (2, 3, 4):
            with patch.object(main_mod, "_run_pipeline"):
                resp = c.post("/webhook/sentry", json=SENTRY_PAYLOAD)
            assert resp.json()["dupe_count"] == expected


class TestApprovalPrSkip:
    """승인 시 category 가 code 가 아니면 PR 생성을 건너뛴다."""

    def _seed_incident(self, c, main_mod, store_mod, category):
        from common.models import IncidentEvent, IncidentStatus, ResolutionReport, Severity, IncidentCategory

        event = IncidentEvent(
            incident_id=f"INC-{category}-1",
            source="sentry",
            title="test",
            raw_payload={},
        )
        main_mod.incident_store.add(event)
        report = ResolutionReport(
            incident_id=event.incident_id,
            severity=Severity.HIGH,
            category=IncidentCategory(category),
            triage_summary="t",
            root_cause="r",
            patch_suggestion="p" if category == "code" else "",
            post_mortem_draft="pm",
        )
        main_mod.incident_store.save_report(event.incident_id, report)
        main_mod.incident_store.update_status(
            event.incident_id, IncidentStatus.AWAITING_APPROVAL
        )
        return event.incident_id

    def test_code_category_attempts_pr(self, client, monkeypatch):
        c, main_mod, store_mod = client
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = self._seed_incident(c, main_mod, store_mod, "code")

        resp = c.post(f"/incidents/{incident_id}/approve")
        assert resp.status_code == 200
        body = resp.json()
        assert "pull_request" in body
        assert body["pull_request"]["dry_run"] is True  # credentials 없으면 dry-run

    def test_infra_category_skips_pr(self, client, monkeypatch):
        c, main_mod, store_mod = client
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = self._seed_incident(c, main_mod, store_mod, "infra")

        resp = c.post(f"/incidents/{incident_id}/approve")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pull_request"]["skipped"] is True
        assert "infra" in body["pull_request"]["reason"]

    def test_external_category_skips_pr(self, client, monkeypatch):
        c, main_mod, store_mod = client
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = self._seed_incident(c, main_mod, store_mod, "external")

        resp = c.post(f"/incidents/{incident_id}/approve")
        assert resp.json()["pull_request"]["skipped"] is True


class TestDatadogWebhookDedupe:
    DD_PAYLOAD = {"id": "dd-mon-1", "title": "CPU high", "alert_type": "error"}

    def test_first_datadog_event_accepted(self, client):
        c, main_mod, _ = client
        with patch.object(main_mod, "_run_pipeline") as run:
            resp = c.post("/webhook/datadog", json=self.DD_PAYLOAD)
        assert resp.json()["status"] == "accepted"
        run.assert_called_once()

    def test_datadog_duplicate_skipped(self, client):
        c, main_mod, _ = client
        with patch.object(main_mod, "_run_pipeline"):
            c.post("/webhook/datadog", json=self.DD_PAYLOAD)
        with patch.object(main_mod, "_run_pipeline") as run:
            resp = c.post("/webhook/datadog", json=self.DD_PAYLOAD)
        assert resp.json()["status"] == "duplicate"
        run.assert_not_called()
