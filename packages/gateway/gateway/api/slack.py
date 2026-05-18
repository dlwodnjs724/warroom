"""Slack Interactivity 라우터.

POST ``/slack/interactions`` 한 endpoint 에 Slack 의 모든 interactive payload
가 들어온다 (block_actions, view_submission, ...). Body 는 form-urlencoded
의 ``payload=<json>`` 1개 필드.

Slack 은 3초 이내 200 응답을 요구한다. PR 생성 등 시간 걸리는 작업은
``BackgroundTasks`` 로 위임. ``views.open`` (modal 띄우기) 은 ``trigger_id``
가 3초 TTL 이라 endpoint 안에서 즉시 처리해야 함.

서명 검증:
    X-Slack-Signature + X-Slack-Request-Timestamp →
    HMAC-SHA256("v0:{ts}:{raw_body}", SLACK_SIGNING_SECRET) 일치 확인.
    (실 검증 로직은 ``infrastructure/monitors/security.verify_slack_signature``)
"""

import asyncio
import json
from urllib.parse import parse_qs

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, Response

from gateway.dependencies import get_slack_notifier
from gateway.infrastructure.monitors.security import verify_slack_signature
from gateway.services.decisions import handle_decision

router = APIRouter()


@router.post("/slack/interactions")
async def slack_interactions(
    request: Request,
    background_tasks: BackgroundTasks,
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
):
    body = await request.body()
    if not verify_slack_signature(body, x_slack_signature, x_slack_request_timestamp):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    form = parse_qs(body.decode("utf-8"))
    raw_payloads = form.get("payload")
    if not raw_payloads:
        raise HTTPException(status_code=400, detail="Missing payload field")
    try:
        payload = json.loads(raw_payloads[0])
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Invalid payload JSON: {e}") from e

    ptype = payload.get("type")
    if ptype == "block_actions":
        return await _handle_block_actions(payload, background_tasks)
    if ptype == "view_submission":
        return _handle_view_submission(payload, background_tasks)

    # 알 수 없는 payload 는 200 으로 무시 — Slack 은 4xx 시 retry 한다.
    return Response(status_code=200)


async def _handle_block_actions(payload: dict, background_tasks: BackgroundTasks) -> Response:
    """버튼 클릭 — action_id 로 approve/reject 분기."""
    actions = payload.get("actions") or []
    if not actions:
        return Response(status_code=200)
    action = actions[0]
    action_id = action.get("action_id", "")
    incident_id = action.get("value") or ""

    if action_id == "warroom_approve":
        background_tasks.add_task(_approve_in_background, incident_id)
        return Response(status_code=200)

    if action_id == "warroom_reject":
        trigger_id = payload.get("trigger_id") or ""
        if not trigger_id or not incident_id:
            print(
                f"[WARROOM][slack] reject 버튼 — trigger_id/incident_id 누락 "
                f"(incident={incident_id!r}, trigger={'present' if trigger_id else 'missing'})"
            )
            return Response(status_code=200)
        # views.open 은 sync httpx — 이벤트 루프 비점유 위해 to_thread.
        # trigger_id 가 3초 TTL 이라 BackgroundTasks 로 미루지 못한다.
        notifier = get_slack_notifier()
        await asyncio.to_thread(notifier.open_reject_modal, trigger_id, incident_id)
        return Response(status_code=200)

    # 알 수 없는 action_id — 조용히 무시.
    return Response(status_code=200)


def _handle_view_submission(payload: dict, background_tasks: BackgroundTasks) -> Response:
    """Modal 제출 — 반려 사유 추출 후 reject 처리."""
    view = payload.get("view") or {}
    if view.get("callback_id") != "warroom_reject_modal":
        return Response(status_code=200)

    incident_id = view.get("private_metadata") or ""
    state_values = view.get("state", {}).get("values", {})
    reason = (state_values.get("reason_block", {}).get("reason", {}).get("value") or "").strip()
    background_tasks.add_task(_reject_in_background, incident_id, reason or None)
    # 빈 200 응답 → Slack 이 modal 닫음. (validation error 면 response_action 으로 errors 반환)
    return Response(status_code=200)


async def _approve_in_background(incident_id: str) -> None:
    """BackgroundTasks 에서 호출 — handle_decision 의 HTTPException 은 흡수해서 로그만."""
    try:
        await handle_decision(incident_id, approved=True)
    except HTTPException as e:
        print(f"[WARROOM][slack] approve {incident_id} 실패 — status={e.status_code} detail={e.detail}")
    except Exception as e:
        print(f"[WARROOM][slack] approve {incident_id} 예외: {e}")


async def _reject_in_background(incident_id: str, reason: str | None) -> None:
    try:
        await handle_decision(incident_id, approved=False, rejection_reason=reason)
    except HTTPException as e:
        print(f"[WARROOM][slack] reject {incident_id} 실패 — status={e.status_code} detail={e.detail}")
    except Exception as e:
        print(f"[WARROOM][slack] reject {incident_id} 예외: {e}")
