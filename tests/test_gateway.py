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
