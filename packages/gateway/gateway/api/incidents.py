"""인시던트 조회 + Human-in-the-Loop 승인/반려 라우터."""

from fastapi import APIRouter, HTTPException

from gateway.api.incidents_schemas import RejectBody
from gateway.services import incidents as incidents_service
from gateway.services.decisions import handle_decision

router = APIRouter()


@router.get("/incidents")
async def list_incidents():
    """처리된 인시던트 목록 조회."""
    return await incidents_service.list_all()


@router.get("/incidents/{incident_id}")
async def get_incident(incident_id: str):
    entry = await incidents_service.get_by_id(incident_id)
    if not entry:
        raise HTTPException(status_code=404, detail="인시던트를 찾을 수 없습니다.")
    return entry


@router.post("/incidents/{incident_id}/approve")
async def approve_incident(incident_id: str):
    """패치 제안 승인 (Human-in-the-Loop)."""
    return await handle_decision(incident_id, approved=True)


@router.post("/incidents/{incident_id}/reject")
async def reject_incident(incident_id: str, body: RejectBody | None = None):
    """패치 제안 반려 (Human-in-the-Loop)."""
    reason = body.rejection_reason if body else None
    return await handle_decision(incident_id, approved=False, rejection_reason=reason)
