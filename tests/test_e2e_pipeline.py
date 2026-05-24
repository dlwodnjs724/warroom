"""End-to-end 통합 — webhook → mock pipeline → approve → PR dry-run.

기존 test_gateway 는 ``run_incident_pipeline`` 자체를 patch 해 webhook
응답만 검증한다. 이 파일은 그 반대 — 파이프라인을 실제로 돌려 status
전이 (PENDING → ANALYZING → AWAITING_APPROVAL) + report 영속화 + approve
가 dry-run PR 까지 가는 전 경로를 한 테스트로 묶는다.

LLM 호출 봉인은 ``orchestrator.runner._USE_MOCK = True`` (module-level
attribute patch). mock 내부 ``time.sleep`` 도 no-op 처리해 테스트 지연 제거.
"""

import pytest
from common.models import IncidentStatus
from fastapi.testclient import TestClient


@pytest.fixture
def e2e_client(monkeypatch):
    """진짜 pipeline (mock 응답) 이 백그라운드에서 도는 client.

    test_gateway 의 ``client`` 와 달리 ``MOCK_PIPELINE`` env 만으로는 부족
    하다 — ``orchestrator.runner._USE_MOCK`` 는 module-level 에서 한 번만
    평가되므로 attribute patch 가 필요.
    """
    import orchestrator.runner as runner_mod

    monkeypatch.setattr(runner_mod, "_USE_MOCK", True)
    # mock pipeline 의 time.sleep(1) × 4 = 4초 → 0초.
    monkeypatch.setattr(runner_mod.time, "sleep", lambda _s: None)
    monkeypatch.setenv("GITHUB_REPO", "owner/demo")

    import gateway.main as main_mod

    with TestClient(main_mod.app) as c:
        yield c


SENTRY_PAYLOAD = {
    "data": {"issue": {"id": "e2e-pipe-1", "title": "NPE in stripe.charge", "level": "error"}},
    "action": "created",
}


async def test_webhook_to_approve_full_path(e2e_client):
    """webhook → mock pipeline → AWAITING_APPROVAL → approve → PR dry-run.

    Starlette ``TestClient`` 가 BackgroundTasks 를 동기 실행하므로,
    ``client.post`` 가 반환된 시점엔 파이프라인도 끝나있다.
    """
    from gateway.infrastructure.db.repository import get_repository

    # 1) webhook 수신
    resp = e2e_client.post("/webhook/sentry", json=SENTRY_PAYLOAD)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    incident_id = body["incident_id"]

    # 2) 파이프라인 결과 — AWAITING_APPROVAL + report 영속화
    repo = get_repository()
    entry = await repo.get(incident_id)
    assert entry is not None, "webhook 후 incident 가 DB 에 없음"
    actual_status = entry["status"]
    expected_status = IncidentStatus.AWAITING_APPROVAL
    assert actual_status == expected_status, f"status={actual_status} (expected {expected_status})"
    assert entry["report"] is not None
    assert entry["report"]["category"] == "code"
    assert entry["report"]["patch_suggestion"], "mock pipeline 이 patch_suggestion 채워줘야 함"

    # 3) approve → PR dry-run
    resp2 = e2e_client.post(f"/incidents/{incident_id}/approve")
    assert resp2.status_code == 200
    approve_body = resp2.json()
    assert approve_body["status"] == "approved"
    pr = approve_body["pull_request"]
    assert pr["dry_run"] is True
    assert pr.get("url"), "dry-run 도 가짜 URL 은 반환해야 함"

    # 4) 최종 status — APPROVED + report.is_approved=True
    final = await repo.get(incident_id)
    assert final["status"] == IncidentStatus.APPROVED
    assert final["report"]["is_approved"] is True
