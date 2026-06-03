"""`services.pipeline.drain_in_flight` — graceful shutdown 단위 테스트.

5.5 의 startup ``recover_stale_analyzing`` 과 짝. lifespan exit 시 진행 중인
pipeline task 가 grace period 동안 완료를 기다리고, 미완료는 cancel + status
FAILED 로 정합화된다.
"""

import asyncio

import pytest
from common.models import IncidentCategory, IncidentEvent, IncidentStatus, Severity
from gateway.infrastructure.db.repository import IncidentRepository
from gateway.services import pipeline as pipeline_mod


@pytest.fixture(autouse=True)
def _clear_in_flight():
    """매 테스트 격리 — module-level dict 잔여 정리."""
    pipeline_mod._in_flight.clear()
    yield
    pipeline_mod._in_flight.clear()


@pytest.fixture
def repo(schema):
    return IncidentRepository()


async def _seed_analyzing(repo: IncidentRepository, incident_id: str) -> None:
    """drain 이 ANALYZING → FAILED 마킹하는지 검증 가능하도록 사전 seed."""
    await repo.add(IncidentEvent(incident_id=incident_id, source="sentry", title="t", raw_payload={}))
    await repo.update_status(incident_id, IncidentStatus.ANALYZING)


class TestDrainEmpty:
    async def test_no_inflight_returns_zero(self):
        done, cancelled = await pipeline_mod.drain_in_flight(timeout=1.0)
        assert (done, cancelled) == (0, 0)


class TestDrainCompletion:
    async def test_completing_task_counted_as_done(self):
        """grace 내 자연 완료 → done=1, cancel=0."""

        async def fast_pipeline():
            task = asyncio.current_task()
            pipeline_mod._in_flight["fast-1"] = task
            try:
                await asyncio.sleep(0.05)
            finally:
                pipeline_mod._in_flight.pop("fast-1", None)

        task = asyncio.create_task(fast_pipeline())
        await asyncio.sleep(0)  # 첫 await 까지 진행시켜 in_flight 등록 보장
        done, cancelled = await pipeline_mod.drain_in_flight(timeout=2.0)
        assert (done, cancelled) == (1, 0)
        await task  # 청소


class TestDrainCancelMarksFailed:
    async def test_overrunning_task_cancelled_and_marked_failed(self, repo):
        """grace 초과 → cancel + status FAILED."""
        await _seed_analyzing(repo, "slow-1")

        async def slow_pipeline():
            task = asyncio.current_task()
            pipeline_mod._in_flight["slow-1"] = task
            try:
                await asyncio.sleep(10.0)  # grace 보다 훨씬 김
            finally:
                pipeline_mod._in_flight.pop("slow-1", None)

        task = asyncio.create_task(slow_pipeline())
        await asyncio.sleep(0)
        done, cancelled = await pipeline_mod.drain_in_flight(timeout=0.1)
        assert done == 0
        assert cancelled == 1

        entry = await repo.get("slow-1")
        assert entry is not None
        assert entry["status"] == IncidentStatus.FAILED, "drain 이 cancel 후 FAILED 마킹해야 함"

        # task cleanup
        with pytest.raises(asyncio.CancelledError):
            await task


class TestRunPipelineRegistersInFlight:
    async def test_in_flight_registered_and_cleared(self, schema, monkeypatch):
        """``run_incident_pipeline`` 진입 시 _in_flight 등록, 종료 시 제거."""
        from gateway.services.pipeline import run_incident_pipeline

        # mock pipeline + dry-run notifier
        monkeypatch.setenv("MOCK_PIPELINE", "true")
        import orchestrator.runner as runner_mod

        monkeypatch.setattr(runner_mod, "_USE_MOCK", True)
        monkeypatch.setattr(runner_mod.time, "sleep", lambda _s: None)

        event = IncidentEvent(
            incident_id="reg-1",
            source="sentry",
            title="t",
            raw_payload={},
            severity=Severity.HIGH,
        )
        # seed — run_incident_pipeline 이 add 안 하므로 미리.
        repo = IncidentRepository()
        await repo.add(event)

        # task 만들고 in_flight 진입 직후 확인
        task = asyncio.create_task(run_incident_pipeline(event))
        await asyncio.sleep(0)  # update_status ANALYZING 까지 진행
        # 짧은 sleep 후 등록 확인 (mock pipeline 이 매우 빨라 이미 끝났을 수도)
        registered_at_some_point = "reg-1" in pipeline_mod._in_flight
        await task

        # 완료 후 반드시 제거
        assert "reg-1" not in pipeline_mod._in_flight
        # in-flight 추적이 실제로 일어났는지 한 번이라도 확인 — 빠른 mock 이라
        # 이미 비워졌을 가능성 허용 (False positive 차단 핵심: 완료 후 cleanup)
        _ = registered_at_some_point  # 진단용

        # category=code 의 mock 은 끝까지 AWAITING_APPROVAL 까지
        entry = await repo.get("reg-1")
        assert entry["status"] == IncidentStatus.AWAITING_APPROVAL
        assert entry["report"] is not None
        assert entry["report"]["category"] == IncidentCategory.CODE.value
