"""Webhook 인제스천 — dedupe 처리 후 백그라운드 파이프라인 트리거."""

from common.models import IncidentEvent
from fastapi import BackgroundTasks

from gateway.infrastructure.db.repository import get_repository
from gateway.services.pipeline import run_incident_pipeline


async def ingest_event(event: IncidentEvent, background_tasks: BackgroundTasks) -> dict:
    """파싱된 IncidentEvent 를 dedupe 처리 후 파이프라인에 흘려보낸다."""
    repo = get_repository()
    existing = await repo.get(event.incident_id)
    if existing:
        count = await repo.increment_dupe_count(event.incident_id)
        print(f"[WARROOM] 중복 수신 — {event.incident_id} (총 {count}건). 기존 파이프라인 유지.")
        return {
            "incident_id": event.incident_id,
            "status": "duplicate",
            "dupe_count": count,
        }
    await repo.add(event)
    background_tasks.add_task(run_incident_pipeline, event)
    return {"incident_id": event.incident_id, "status": "accepted"}
