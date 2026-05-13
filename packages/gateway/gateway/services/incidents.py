"""인시던트 조회 서비스 — read-only query.

API layer 는 repository 를 직접 호출하지 않는다 (architecture.md 룰).
응답 변환 / 필터 / 권한 체크가 들어갈 자리.
"""

from gateway.infrastructure.db.repository import get_repository


async def list_all() -> list[dict]:
    return await get_repository().list_all()


async def get_by_id(incident_id: str) -> dict | None:
    return await get_repository().get(incident_id)
