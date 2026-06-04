"""인시던트 파이프라인 실행 — ANALYZING → 분석 → AWAITING_APPROVAL / FAILED.

CrewAI 가 sync 라 워커 스레드에서 돌아가야 하고, SlackNotifier 도 sync.
Slack thread 영속화 (async store 호출) 는 메인 루프 참조를 통해 브리지한다.

`set_main_loop()` 는 `gateway.main` 의 lifespan 진입 시 호출돼야 한다.

Graceful shutdown — in-flight pipeline 추적: ``_in_flight`` (incident_id → Task)
가 BackgroundTask 의 진행 상황을 추적한다. lifespan exit 시 ``drain_in_flight()``
가 grace period 동안 wait, 미완료는 cancel + status FAILED. 5.5 의 startup
복구 (recover_stale_analyzing) 와 짝 — Ctrl+C / 재배포 시 데이터 정합성 유지.
"""

import asyncio
import logging

from common.models import IncidentEvent, IncidentStatus

from gateway.infrastructure.db.repository import get_repository

logger = logging.getLogger(__name__)

_main_loop: asyncio.AbstractEventLoop | None = None
_in_flight: dict[str, asyncio.Task] = {}


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
    from orchestrator.runner import run_pipeline

    from gateway.dependencies import make_pipeline_notifier

    repo = get_repository()
    notifier = make_pipeline_notifier(persist_cb=_persist_slack_thread, lookup_cb=_lookup_slack_thread)

    # in-flight 추적 — graceful shutdown 시 drain 대상.
    task = asyncio.current_task()
    if task is not None:
        _in_flight[event.incident_id] = task

    try:
        await repo.update_status(event.incident_id, IncidentStatus.ANALYZING)
        # incident 알림 송신 — sync 한 httpx 호출이라 to_thread 로 이벤트 루프 비점유.
        await asyncio.to_thread(notifier.on_incident_received, event)
        report = await asyncio.to_thread(run_pipeline, event, notifier)
        await repo.save_report(event.incident_id, report)
        await repo.update_status(event.incident_id, IncidentStatus.AWAITING_APPROVAL)
    except asyncio.CancelledError:
        # shutdown drain timeout 으로 cancel — status FAILED 는 drain 측이 마킹.
        # 여기서 update_status 하면 cancel 후 새 await 발생해 코루틴이 재실행됨.
        raise
    except Exception as e:
        logger.exception("파이프라인 실패 (%s): %s", event.incident_id, e)
        await repo.update_status(event.incident_id, IncidentStatus.FAILED)
        try:
            await asyncio.to_thread(notifier.on_pipeline_failed, event.incident_id, str(e))
        except Exception:
            logger.exception("on_pipeline_failed 콜백 실패")
    finally:
        _in_flight.pop(event.incident_id, None)


async def drain_in_flight(timeout: float = 30.0) -> tuple[int, int]:
    """lifespan exit 시 — 진행 중인 pipeline 의 완료를 grace period 동안 대기.

    timeout 내 완료 안 한 task 는 cancel + 해당 incident status 를 FAILED 로
    정합화. 반환: (정상 종료 수, cancel 된 수).

    Ctrl+C / 컨테이너 종료 / 재배포 시 ``ANALYZING`` 으로 영영 stuck 되는 케이스를
    방지. 다음 startup 의 ``recover_stale_analyzing`` 과 짝 (정합화 1차 시도).
    """
    if not _in_flight:
        return 0, 0
    snapshot = list(_in_flight.items())  # iterate 중 pop 안전
    tasks = [t for _, t in snapshot]
    done, still = await asyncio.wait(tasks, timeout=timeout)

    cancelled_ids: list[str] = []
    for incident_id, task in snapshot:
        if task in still:
            task.cancel()
            cancelled_ids.append(incident_id)

    if cancelled_ids:
        repo = get_repository()
        for inc_id in cancelled_ids:
            try:
                await repo.update_status(inc_id, IncidentStatus.FAILED)
            except Exception:
                logger.exception("shutdown FAILED 마킹 실패 (%s)", inc_id)

    return len(done), len(cancelled_ids)
