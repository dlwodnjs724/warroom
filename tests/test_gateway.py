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

# services.ingest 가 BackgroundTasks 에 추가하는 함수. 여기를 patch 해 파이프라인을 봉인.
_PIPELINE_TARGET = "gateway.services.ingest.run_incident_pipeline"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("MOCK_PIPELINE", "true")
    import gateway.main as main_mod

    with TestClient(main_mod.app) as c:
        yield c


SENTRY_PAYLOAD = {
    "data": {"issue": {"id": "sentry-dupe-1", "title": "NPE", "level": "error"}},
    "action": "created",
}


class TestSentryWebhookDedupe:
    def test_first_event_is_accepted_and_runs_pipeline(self, client):
        with patch(_PIPELINE_TARGET) as run:
            resp = client.post("/webhook/sentry", json=SENTRY_PAYLOAD)

        assert resp.status_code == 202
        assert resp.json()["status"] == "accepted"
        assert resp.json()["incident_id"] == "sentry-dupe-1"
        run.assert_called_once()

    def test_duplicate_event_returns_duplicate_without_pipeline(self, client):
        with patch(_PIPELINE_TARGET):
            client.post("/webhook/sentry", json=SENTRY_PAYLOAD)

        with patch(_PIPELINE_TARGET) as run:
            resp = client.post("/webhook/sentry", json=SENTRY_PAYLOAD)

        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "duplicate"
        assert body["dupe_count"] == 2
        run.assert_not_called()

    def test_repeated_duplicates_increment_counter(self, client):
        with patch(_PIPELINE_TARGET):
            client.post("/webhook/sentry", json=SENTRY_PAYLOAD)
        for expected in (2, 3, 4):
            with patch(_PIPELINE_TARGET):
                resp = client.post("/webhook/sentry", json=SENTRY_PAYLOAD)
            assert resp.json()["dupe_count"] == expected


class TestApprovalPrSkip:
    """승인 시 category 가 code 가 아니면 PR 생성을 건너뛴다."""

    async def _seed_incident(self, category):
        from common.models import (
            IncidentCategory,
            IncidentEvent,
            IncidentStatus,
            ResolutionReport,
            Severity,
        )
        from gateway.infrastructure.db.repository import get_repository

        repo = get_repository()
        event = IncidentEvent(
            incident_id=f"INC-{category}-1",
            source="sentry",
            title="test",
            raw_payload={},
        )
        await repo.add(event)
        report = ResolutionReport(
            incident_id=event.incident_id,
            severity=Severity.HIGH,
            category=IncidentCategory(category),
            triage_summary="t",
            root_cause="r",
            patch_suggestion="p" if category == "code" else "",
            post_mortem_draft="pm",
        )
        await repo.save_report(event.incident_id, report)
        await repo.update_status(event.incident_id, IncidentStatus.AWAITING_APPROVAL)
        return event.incident_id

    async def test_code_category_attempts_pr(self, client, monkeypatch):
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = await self._seed_incident("code")

        resp = client.post(f"/incidents/{incident_id}/approve")
        assert resp.status_code == 200
        body = resp.json()
        assert "pull_request" in body
        assert body["pull_request"]["dry_run"] is True

    async def test_infra_category_skips_pr(self, client, monkeypatch):
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = await self._seed_incident("infra")

        resp = client.post(f"/incidents/{incident_id}/approve")
        body = resp.json()
        assert body["pull_request"]["skipped"] is True
        assert "infra" in body["pull_request"]["reason"]

    async def test_external_category_skips_pr(self, client, monkeypatch):
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = await self._seed_incident("external")

        resp = client.post(f"/incidents/{incident_id}/approve")
        assert resp.json()["pull_request"]["skipped"] is True


class TestRejectPrCleanup:
    """반려 시 영속화된 PR 정보가 있으면 close + branch 삭제 시도."""

    async def _seed_awaiting(self, incident_id="INC-REJECT-1"):
        from common.models import (
            IncidentCategory,
            IncidentEvent,
            IncidentStatus,
            ResolutionReport,
            Severity,
        )
        from gateway.infrastructure.db.repository import get_repository

        repo = get_repository()
        event = IncidentEvent(incident_id=incident_id, source="sentry", title="t", raw_payload={})
        await repo.add(event)
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

    async def test_reject_with_pr_info_calls_close(self, client, monkeypatch, tmp_path):
        from gateway.dependencies import reset_github_client
        from gateway.infrastructure.db.repository import get_repository

        log_path = tmp_path / "gh.jsonl"
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        monkeypatch.setenv("GITHUB_DRY_RUN_LOG", str(log_path))
        # cached DryRun client (lifespan 에서 default path 로 생성됨) 무효화
        reset_github_client()

        incident_id = await self._seed_awaiting()
        repo = get_repository()
        await repo.set_pr_info(incident_id, 99, "warroom/incident-INC-REJECT-1-x")

        resp = client.post(f"/incidents/{incident_id}/reject")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "rejected"
        assert body["pr_closed"]["number"] == 99
        assert body["pr_closed"]["closed"] is True

        # dry-run client 가 close_pr 페이로드 기록했는지
        lines = log_path.read_text().splitlines()
        assert any(json.loads(line).get("action") == "close_pr" for line in lines)

    async def test_reject_without_pr_info_silently_skips_close(self, client, monkeypatch):
        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = await self._seed_awaiting("INC-REJECT-NOPR-1")

        resp = client.post(f"/incidents/{incident_id}/reject")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "rejected"
        assert "pr_closed" not in body  # PR 정보 없으면 cleanup 시도 안 함

    async def test_approve_persists_pr_info(self, client, monkeypatch):
        from gateway.infrastructure.db.repository import get_repository

        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = await self._seed_awaiting("INC-APPROVE-PERSIST-1")

        resp = client.post(f"/incidents/{incident_id}/approve")
        assert resp.status_code == 200

        # dry-run 은 pr_number=None → set_pr_info 미호출
        repo = get_repository()
        pr_info = await repo.get_pr_info(incident_id)
        assert pr_info is None  # dry-run 은 pr_number 없음

    async def test_approve_pr_persist_failure_surfaces_warning(self, client, monkeypatch):
        """set_pr_info 실패해도 PR 자체는 성공 → endpoint 정상 응답 + warning."""
        from gateway.infrastructure.db.repository import IncidentRepository

        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        incident_id = await self._seed_awaiting("INC-PERSIST-FAIL-1")

        # _open_pr 가 실 PR 번호/브랜치를 돌려줄 수 있게 mock
        from gateway.services import decisions as dec_mod

        def fake_open_pr(entry, client, github_repo):
            return {
                "url": "https://github.com/owner/demo/pull/77",
                "branch": "warroom/incident-X",
                "number": 77,
                "dry_run": False,
            }

        monkeypatch.setattr(dec_mod, "_open_pr", fake_open_pr)

        # set_pr_info 가 transient DB 에러로 실패
        async def boom(self, *args, **kwargs):
            raise RuntimeError("DB write failed")

        monkeypatch.setattr(IncidentRepository, "set_pr_info", boom)

        resp = client.post(f"/incidents/{incident_id}/approve")
        assert resp.status_code == 200
        body = resp.json()
        # PR 정보는 그대로 응답에 + warning 노출
        assert body["pull_request"]["number"] == 77
        assert "pr_persist_warning" in body["pull_request"]

    async def test_reject_close_pr_auth_error_surfaces_status_code(self, client, monkeypatch):
        """close_pr 가 401/403 일 때 error_type=auth + status_code 응답."""
        from gateway.dependencies import reset_github_client
        from gateway.infrastructure.db.repository import get_repository
        from github.base import GitHubAuthError

        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        reset_github_client()

        incident_id = await self._seed_awaiting("INC-CLOSE-AUTH-1")
        repo = get_repository()
        await repo.set_pr_info(incident_id, 55, "warroom/incident-AUTH")

        # 캐시된 dry-run client 의 close_pr 만 401 raise 하도록 패치
        from gateway.dependencies import get_github_client

        ghc = get_github_client()

        def boom(repo, pr_number, branch):
            raise GitHubAuthError("토큰 만료", 401)

        monkeypatch.setattr(ghc, "close_pr", boom)

        resp = client.post(f"/incidents/{incident_id}/reject")
        assert resp.status_code == 200
        pr_closed = resp.json()["pr_closed"]
        assert pr_closed["number"] == 55
        assert pr_closed["error_type"] == "auth"
        assert pr_closed["status_code"] == 401

    async def test_reject_close_pr_transient_error_surfaces_status_code(self, client, monkeypatch):
        """close_pr 가 5xx 일 때 error_type=transient + status_code 응답."""
        from gateway.dependencies import get_github_client, reset_github_client
        from gateway.infrastructure.db.repository import get_repository
        from github.base import GitHubTransientError

        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        reset_github_client()

        incident_id = await self._seed_awaiting("INC-CLOSE-5XX-1")
        repo = get_repository()
        await repo.set_pr_info(incident_id, 66, "warroom/incident-5XX")

        ghc = get_github_client()

        def boom(repo, pr_number, branch):
            raise GitHubTransientError("bad gateway", 502)

        monkeypatch.setattr(ghc, "close_pr", boom)

        resp = client.post(f"/incidents/{incident_id}/reject")
        assert resp.status_code == 200
        pr_closed = resp.json()["pr_closed"]
        assert pr_closed["error_type"] == "transient"
        assert pr_closed["status_code"] == 502

    async def test_reject_close_pr_runs_in_worker_thread(self, client, monkeypatch):
        """close_pr (sync httpx) 가 async endpoint 의 이벤트 루프를 점유하지 않는다.

        to_thread 위임을 검증하기 위해 close_pr 안에서 현재 스레드가 메인 스레드가
        아님을 확인 — async path 의 이벤트 루프와 분리된 워커 스레드에서 실행됨.
        """
        import threading

        from gateway.dependencies import get_github_client, reset_github_client
        from gateway.infrastructure.db.repository import get_repository

        monkeypatch.setenv("GITHUB_REPO", "owner/demo")
        reset_github_client()

        incident_id = await self._seed_awaiting("INC-CLOSE-THREAD-1")
        repo = get_repository()
        await repo.set_pr_info(incident_id, 88, "warroom/incident-THREAD")

        main_thread = threading.get_ident()
        observed: dict = {}

        ghc = get_github_client()
        orig = ghc.close_pr

        def trace(repo, pr_number, branch):
            observed["thread"] = threading.get_ident()
            return orig(repo, pr_number, branch)

        monkeypatch.setattr(ghc, "close_pr", trace)

        resp = client.post(f"/incidents/{incident_id}/reject")
        assert resp.status_code == 200
        # close_pr 호출 스레드 != 테스트 (main) 스레드 → to_thread 위임됨
        assert observed["thread"] != main_thread


class TestWebhookSignatureVerification:
    def test_sentry_rejects_invalid_signature(self, client, monkeypatch):
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "topsecret")
        resp = client.post(
            "/webhook/sentry",
            json=SENTRY_PAYLOAD,
            headers={"Sentry-Hook-Signature": "wrong-sig"},
        )
        assert resp.status_code == 401

    def test_sentry_rejects_missing_signature(self, client, monkeypatch):
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "topsecret")
        resp = client.post("/webhook/sentry", json=SENTRY_PAYLOAD)
        assert resp.status_code == 401

    def test_sentry_accepts_valid_signature(self, client, monkeypatch):
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "topsecret")
        body = json.dumps(SENTRY_PAYLOAD).encode("utf-8")
        sig = hmac.new(b"topsecret", body, hashlib.sha256).hexdigest()

        with patch(_PIPELINE_TARGET):
            resp = client.post(
                "/webhook/sentry",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "Sentry-Hook-Signature": sig,
                },
            )
        assert resp.status_code == 202

    def test_datadog_rejects_wrong_token(self, client, monkeypatch):
        monkeypatch.setenv("WARROOM_DATADOG_TOKEN", "shared-xyz")
        resp = client.post(
            "/webhook/datadog",
            json={"id": "x", "title": "t"},
            headers={"X-Warroom-Token": "nope"},
        )
        assert resp.status_code == 401

    def test_datadog_accepts_valid_token(self, client, monkeypatch):
        monkeypatch.setenv("WARROOM_DATADOG_TOKEN", "shared-xyz")
        with patch(_PIPELINE_TARGET):
            resp = client.post(
                "/webhook/datadog",
                json={"id": "dd-sig-1", "title": "t"},
                headers={"X-Warroom-Token": "shared-xyz"},
            )
        assert resp.status_code == 202


class TestDatadogWebhookDedupe:
    DD_PAYLOAD = {"id": "dd-mon-1", "title": "CPU high", "alert_type": "error"}

    def test_first_datadog_event_accepted(self, client):
        with patch(_PIPELINE_TARGET) as run:
            resp = client.post("/webhook/datadog", json=self.DD_PAYLOAD)
        assert resp.json()["status"] == "accepted"
        run.assert_called_once()

    def test_datadog_duplicate_skipped(self, client):
        with patch(_PIPELINE_TARGET):
            client.post("/webhook/datadog", json=self.DD_PAYLOAD)
        with patch(_PIPELINE_TARGET) as run:
            resp = client.post("/webhook/datadog", json=self.DD_PAYLOAD)
        assert resp.json()["status"] == "duplicate"
        run.assert_not_called()
