"""Webhook 라우터 — Sentry / Datadog 등 모니터링 도구의 webhook 수신.

엔드포인트는 1초 이내 응답을 보장 (BackgroundTasks 로 파이프라인 비동기 위임).
"""

import json
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from gateway.dependencies import get_datadog_token, get_sentry_secret
from gateway.infrastructure.monitors import datadog as datadog_parser
from gateway.infrastructure.monitors import sentry as sentry_parser
from gateway.services.ingest import ingest_event
from gateway.services.security import verify_datadog_token, verify_sentry_signature

router = APIRouter()


@router.post("/webhook/sentry", status_code=202)
async def webhook_sentry(
    request: Request,
    background_tasks: BackgroundTasks,
    sentry_hook_signature: str | None = Header(default=None),
):
    """Sentry 웹훅 수신 — 즉시 202 반환 후 백그라운드에서 파이프라인 실행."""
    body = await request.body()
    if not verify_sentry_signature(body, sentry_hook_signature, get_sentry_secret()):
        raise HTTPException(status_code=401, detail="Invalid Sentry signature")
    payload = json.loads(body)
    return await ingest_event(sentry_parser.parse(payload), background_tasks)


@router.post("/webhook/datadog", status_code=202)
async def webhook_datadog(
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
    x_warroom_token: str | None = Header(default=None),
):
    """Datadog 웹훅 수신 — Monitor/Incident 페이로드 모두 처리."""
    if not verify_datadog_token(x_warroom_token, get_datadog_token()):
        raise HTTPException(status_code=401, detail="Invalid Datadog token")
    return await ingest_event(datadog_parser.parse(payload), background_tasks)
