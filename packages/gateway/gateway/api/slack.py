"""Slack Interactivity 라우터 — HTTP boundary 만 담당.

POST ``/slack/interactions`` 한 endpoint 에 Slack 의 모든 interactive payload
가 들어온다 (block_actions, view_submission, ...). 본 모듈의 책임은:

1. ``X-Slack-Signature`` / ``X-Slack-Request-Timestamp`` 검증 (raw body 로)
2. ``payload=<json>`` form 필드 → JSON parse
3. ``services.slack_interactions.dispatch`` 에 위임

도메인 분기 (payload type / action_id / reason 추출 / notifier 호출 /
handle_decision 호출) 는 services layer 책임. webhooks.py ↔ ingest_event
패턴과 동일.

서명 검증:
    X-Slack-Signature + X-Slack-Request-Timestamp →
    HMAC-SHA256("v0:{ts}:{raw_body}", SLACK_SIGNING_SECRET) 일치 확인.
    (실 검증 로직은 ``infrastructure/monitors/security.verify_slack_signature``)
"""

import json
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response

from gateway.infrastructure.monitors.security import verify_slack_signature
from gateway.services import slack_interactions

router = APIRouter()


@router.post("/slack/interactions")
async def slack_interactions_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
):
    body = await request.body()
    if not verify_slack_signature(body, x_slack_signature, x_slack_request_timestamp):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid body encoding: {e}") from e

    form = parse_qs(decoded)
    raw_payloads = form.get("payload")
    if not raw_payloads or not raw_payloads[0]:
        raise HTTPException(status_code=400, detail="Missing payload field")
    try:
        payload = json.loads(raw_payloads[0])
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload JSON: {e}") from e

    await slack_interactions.dispatch(payload, background_tasks)
    return Response(status_code=200)
