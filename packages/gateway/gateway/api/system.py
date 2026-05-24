"""횡단 관심사 — health check 등 도메인 외부의 운영 endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness probe. ngrok / load balancer / k8s readiness 호환.

    DB 등 외부 의존성은 확인하지 않는다 — liveness 가 ready 와 분리되어야
    transient DB outage 시 컨테이너가 kill 되는 cascade 를 피한다. readiness
    가 필요해지면 별도 `/readyz` 로 분리.
    """
    return {"status": "ok"}
