"""Slack Interactivity payload 디스패치 (block_actions / view_submission).

api/slack.py 는 HTTP boundary (서명 검증 + body parse) 만 책임지고, 도메인
흐름 결정 (payload type 분기 → action_id 분기 → reason 추출 → notifier
또는 handle_decision 호출) 은 이 모듈에 격리. webhooks.py ↔ ingest_event
관계와 동일 패턴.

§ layering.md § 2: api 는 외부 어댑터 (``SlackNotifier``) 를 직접 호출하지
않는다. composition root 호출은 services 책임 (§ 4c).
"""

import asyncio

from fastapi import BackgroundTasks, HTTPException

from gateway.dependencies import get_slack_notifier
from gateway.services.decisions import handle_decision


async def dispatch(payload: dict, background_tasks: BackgroundTasks) -> None:
    """Slack interactive payload 진입점 — payload type 으로 분기.

    알 수 없는 type 은 silent skip (Slack 은 4xx 시 retry 한다).
    """
    ptype = payload.get("type")
    if ptype == "block_actions":
        await _handle_block_actions(payload, background_tasks)
    elif ptype == "view_submission":
        _handle_view_submission(payload, background_tasks)


async def _handle_block_actions(payload: dict, background_tasks: BackgroundTasks) -> None:
    """버튼 클릭 — action_id 로 approve/reject 분기.

    approve: handle_decision 은 BackgroundTasks 로 (PR 생성 등 시간 걸리는
    작업이 Slack 3초 룰을 어기지 않게).

    reject: ``views.open`` 은 ``trigger_id`` 가 3초 TTL 이라 endpoint 안에서
    즉시 호출 — sync httpx 이므로 ``asyncio.to_thread`` 로 이벤트 루프 비점유.
    """
    actions = payload.get("actions") or []
    if not actions:
        return
    action = actions[0]
    action_id = action.get("action_id", "")
    incident_id = action.get("value") or ""
    # Slack mention 용 — payload.user.id 는 block_actions 에 항상 포함.
    actor_user_id = (payload.get("user") or {}).get("id") or None

    # incident_id 는 approve / reject 양쪽 모두 필요한 공통 요구사항.
    # 누락된 페이로드는 silent skip — Slack 4xx retry 트리거 방지.
    if not incident_id:
        print(f"[WARROOM][slack] {action_id!r} — incident_id (action.value) 누락, skip")
        return

    if action_id == "warroom_approve":
        background_tasks.add_task(_approve_in_background, incident_id, actor_user_id)
        return

    if action_id == "warroom_reject":
        trigger_id = payload.get("trigger_id") or ""
        if not trigger_id:
            print(f"[WARROOM][slack] reject 버튼 — trigger_id 누락 (incident={incident_id!r})")
            return
        notifier = get_slack_notifier()
        await asyncio.to_thread(notifier.open_reject_modal, trigger_id, incident_id)
        return

    # 알 수 없는 action_id — 조용히 무시.


def _handle_view_submission(payload: dict, background_tasks: BackgroundTasks) -> None:
    """Modal 제출 — 반려 사유 추출 후 reject 처리.

    빈 200 응답으로 Slack 이 modal 닫음 (validation error 면 response_action
    으로 errors 반환). 길이 cap / redaction 은 ``handle_decision`` 에서 수행.
    """
    view = payload.get("view") or {}
    if view.get("callback_id") != "warroom_reject_modal":
        return

    incident_id = view.get("private_metadata") or ""
    state_values = view.get("state", {}).get("values", {})
    reason = (state_values.get("reason_block", {}).get("reason", {}).get("value") or "").strip()
    actor_user_id = (payload.get("user") or {}).get("id") or None
    background_tasks.add_task(_reject_in_background, incident_id, reason or None, actor_user_id)


async def _approve_in_background(incident_id: str, actor_user_id: str | None) -> None:
    """BackgroundTasks 에서 호출 — handle_decision 의 HTTPException 흡수 후 로그.

    Slack 에 4xx 가 그대로 보이면 Slack 이 retry 하므로, 도메인 실패는
    응답이 아닌 로그로만 surface.
    """
    try:
        await handle_decision(incident_id, approved=True, actor_user_id=actor_user_id)
    except HTTPException as e:
        print(f"[WARROOM][slack] approve {incident_id} 실패 — " f"status={e.status_code} detail={e.detail}")
    except Exception as e:
        print(f"[WARROOM][slack] approve {incident_id} 예외: {e}")


async def _reject_in_background(incident_id: str, reason: str | None, actor_user_id: str | None) -> None:
    try:
        await handle_decision(
            incident_id, approved=False, rejection_reason=reason, actor_user_id=actor_user_id
        )
    except HTTPException as e:
        print(f"[WARROOM][slack] reject {incident_id} 실패 — " f"status={e.status_code} detail={e.detail}")
    except Exception as e:
        print(f"[WARROOM][slack] reject {incident_id} 예외: {e}")
