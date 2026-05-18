"""인시던트 파이프라인 실행 — ANALYZING → 분석 → AWAITING_APPROVAL / FAILED.

CrewAI 가 sync 라 워커 스레드에서 돌아가야 하고, SlackNotifier 도 sync.
Slack thread 영속화 (async store 호출) 는 메인 루프 참조를 통해 브리지한다.

`set_main_loop()` 는 `gateway.main` 의 lifespan 진입 시 호출돼야 한다.
"""

import asyncio

from common.models import IncidentEvent, IncidentStatus

from gateway.infrastructure.db.repository import get_repository

_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    global _main_loop
    _main_loop = loop


def _persist_slack_thread(incident_id: str, channel_id: str, ts: str) -> None:
    """SlackNotifier 에서 호출되는 sync 콜백 — async repository 메서드를 메인 루프에 스케줄."""
    if _main_loop is None:
        return
    asyncio.run_coroutine_threadsafe(
        get_repository().set_slack_thread(incident_id, channel_id, ts), _main_loop
    )


def _lookup_slack_thread(incident_id: str) -> tuple[str, str] | None:
    if _main_loop is None:
        return None
    fut = asyncio.run_coroutine_threadsafe(get_repository().get_slack_thread(incident_id), _main_loop)
    try:
        return fut.result(timeout=5.0)
    except Exception:
        return None


async def run_incident_pipeline(event: IncidentEvent) -> None:
    # orchestrator는 import 지연 (LLM 초기화 비용)
    from chatops.clients.factory import make_notifier
    from orchestrator.runner import run_pipeline

    repo = get_repository()
    notifier = make_notifier(persist_cb=_persist_slack_thread, lookup_cb=_lookup_slack_thread)
    try:
        await repo.update_status(event.incident_id, IncidentStatus.ANALYZING)
        # incident 알림 송신 — sync 한 httpx 호출이라 to_thread 로 이벤트 루프 비점유.
        await asyncio.to_thread(notifier.on_incident_received, event)
        report = await asyncio.to_thread(run_pipeline, event, notifier)
        await repo.save_report(event.incident_id, report)
        await repo.update_status(event.incident_id, IncidentStatus.AWAITING_APPROVAL)
    except Exception as e:
        print(f"[ERROR] 파이프라인 실패 ({event.incident_id}): {e}")
        await repo.update_status(event.incident_id, IncidentStatus.FAILED)
        try:
            await asyncio.to_thread(notifier.on_pipeline_failed, event.incident_id, str(e))
        except Exception as cb_err:
            print(f"[ERROR] on_pipeline_failed 콜백 실패: {cb_err}")
