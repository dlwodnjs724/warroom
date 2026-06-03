"""Human-in-the-Loop 승인/반려 결정 + 승인 시 GitHub PR 트리거."""

import asyncio
from datetime import datetime

from common.models import IncidentCategory, IncidentStatus, ResolutionReport, Severity
from common.redact import redact_secrets
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from github.base import GitHubAuthError, GitHubClient, GitHubError, GitHubTransientError
from github.pr_builder import build_patch_pr

from gateway.dependencies import get_github_client, get_github_repo, make_pipeline_notifier
from gateway.infrastructure.db.repository import get_repository
from gateway.services.pipeline import _lookup_slack_thread

# rejection_reason 은 Slack modal 자유 입력 → DB 영속화 + HTTP 응답 echo + 추후 LLM
# 컨텍스트 주입 (재분석 hook 도입 시) 까지 흐르므로, secrets.md § 3 의 redaction
# 정책 대상. 영속화/응답 직전 단일 지점 (handle_decision) 에서 통과시킨다.
_REJECT_REASON_MAX = 4000  # Slack plain_text_input 기본 한도 ~3000자 + 여유


async def handle_decision(
    incident_id: str,
    approved: bool,
    rejection_reason: str | None = None,
    actor_user_id: str | None = None,
) -> JSONResponse:
    repo = get_repository()
    entry = await repo.get(incident_id)
    if not entry:
        raise HTTPException(status_code=404, detail="인시던트를 찾을 수 없습니다.")
    if entry["status"] != IncidentStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"승인 대기 상태가 아닙니다. 현재 상태: {entry['status']}",
        )

    status = IncidentStatus.APPROVED if approved else IncidentStatus.REJECTED
    # approve 경로는 reason 입력 무시. reject 경로는 redact (secret 패턴 마스킹) →
    # 길이 캡 (DoS / column blowup 방지) → 영속화. None / 빈 문자열은 영속화 skip.
    reason_to_persist: str | None = None
    if not approved and rejection_reason:
        redacted = redact_secrets(rejection_reason)
        reason_to_persist = redacted[:_REJECT_REASON_MAX]
    await repo.update_status(
        incident_id,
        status,
        is_approved=approved,
        rejection_reason=reason_to_persist,
    )

    action = "승인" if approved else "반려"
    if reason_to_persist:
        print(f"[WARROOM] 인시던트 {incident_id} {action} 처리 완료 (사유: {reason_to_persist})")
    else:
        print(f"[WARROOM] 인시던트 {incident_id} {action} 처리 완료")

    # Slack thread 에 결정 사실 + (반려 시) 사유 echo. PR 결과는 PR 생성/close 직후
    # 추가 reply 로 송신. notifier 가 dry-run (token 없음) 이면 stdout JSONL 만 찍힘.
    # pipeline 과 동일하게 thread_lookup 콜백을 wiring — 이 인스턴스는 webhook 을
    # 받지 않았으므로 in-memory 캐시는 비어있고, DB lookup 으로만 thread_ts 확보 가능.
    notifier = make_pipeline_notifier(lookup_cb=_lookup_slack_thread)
    # actor mention — Slack interactivity payload 의 user.id (`<@USERID>` 형식 멘션).
    # 없으면 빈 prefix (CLI 직접 호출 / 테스트 경로 등).
    actor = f"<@{actor_user_id}> " if actor_user_id else ""
    if approved:
        await asyncio.to_thread(
            notifier.on_agent_update,
            incident_id,
            "WARROOM",
            f"✅ {actor}승인 — GitHub PR 생성 진행 중...",
        )
    elif reason_to_persist:
        await asyncio.to_thread(
            notifier.on_agent_update,
            incident_id,
            "WARROOM",
            f"❌ {actor}반려 — 사유: {reason_to_persist}",
        )
    else:
        await asyncio.to_thread(
            notifier.on_agent_update,
            incident_id,
            "WARROOM",
            f"❌ {actor}반려 (사유 미입력)",
        )

    response: dict[str, object] = {
        "incident_id": incident_id,
        "status": status,
        "action": action,
    }
    if reason_to_persist:
        response["rejection_reason"] = reason_to_persist

    github_client = get_github_client()
    github_repo = get_github_repo()

    if approved:
        # _open_pr 는 sync (build_patch_pr 내부가 sync httpx). async endpoint 의
        # 이벤트 루프 점유를 피하려면 to_thread 위임 — 1초 룰 / async.md § 1.
        # transport 실패도 분류된 예외로 surface — reject 경로와 대칭.
        try:
            pr_result = await asyncio.to_thread(_open_pr, entry, github_client, github_repo)
        except GitHubAuthError as e:
            print(
                f"[WARROOM][open_pr] {incident_id} PR 생성 실패 — "
                f"error_type=auth status_code={e.status_code} (토큰 회전 / 권한 점검 필요): {e}"
            )
            await asyncio.to_thread(
                notifier.on_agent_update,
                incident_id,
                "WARROOM",
                f"⚠️ PR 생성 실패 (auth, {e.status_code}) — 토큰/권한 점검 필요",
            )
            response["pull_request"] = {
                "error": str(e),
                "error_type": "auth",
                "status_code": e.status_code,
            }
            return JSONResponse(response)
        except GitHubTransientError as e:
            retry_hint = f" retry_after={e.retry_after}s" if e.retry_after is not None else ""
            print(
                f"[WARROOM][open_pr] {incident_id} PR 생성 실패 — "
                f"error_type=transient status_code={e.status_code}{retry_hint} (재시도 가치 있음): {e}"
            )
            await asyncio.to_thread(
                notifier.on_agent_update,
                incident_id,
                "WARROOM",
                f"⚠️ PR 생성 실패 (transient, {e.status_code}) — 재시도 가치 있음"
                + (f" / {int(e.retry_after)}초 후 재시도 권장" if e.retry_after else ""),
            )
            pr_payload = {
                "error": str(e),
                "error_type": "transient",
                "status_code": e.status_code,
            }
            if e.retry_after is not None:
                pr_payload["retry_after"] = e.retry_after
            response["pull_request"] = pr_payload
            return JSONResponse(response)
        except GitHubError as e:
            print(
                f"[WARROOM][open_pr] {incident_id} PR 생성 실패 — "
                f"error_type=http status_code={e.status_code}: {e}"
            )
            await asyncio.to_thread(
                notifier.on_agent_update,
                incident_id,
                "WARROOM",
                f"⚠️ PR 생성 실패 (http {e.status_code})",
            )
            response["pull_request"] = {
                "error": str(e),
                "error_type": "http",
                "status_code": e.status_code,
            }
            return JSONResponse(response)
        if pr_result and isinstance(pr_result.get("number"), int) and pr_result.get("branch"):
            # PR 은 이미 GitHub 에 만들어졌으므로 DB 영속화 실패가 endpoint
            # 전체를 500 으로 떨어뜨리면 PR 이 고아 (DB 모르고 GitHub 만 알고
            # 있는 상태) 가 되어 reject 시 cleanup 불가. 영속화 실패는
            # warning 으로 surface 하고 endpoint 는 정상 응답.
            try:
                await repo.set_pr_info(incident_id, pr_result["number"], pr_result["branch"])
            except Exception as e:
                print(
                    f"[WARROOM] PR 영속화 실패 — incident_id={incident_id} "
                    f"pr_number={pr_result['number']} branch={pr_result['branch']}: {e}"
                )
                pr_result["pr_persist_warning"] = (
                    "PR 생성 성공했으나 DB 기록 실패. 반려 시 자동 cleanup 불가 — 수동 close 필요."
                )
        if pr_result:
            response["pull_request"] = pr_result
            # 성공 / skip / dry-run 분기별 thread reply
            if pr_result.get("skipped"):
                await asyncio.to_thread(
                    notifier.on_agent_update,
                    incident_id,
                    "WARROOM",
                    f"⏭ PR 생성 skip — {pr_result.get('reason')}",
                )
            elif isinstance(pr_result.get("number"), int):
                dry = " (dry-run)" if pr_result.get("dry_run") else ""
                await asyncio.to_thread(
                    notifier.on_agent_update,
                    incident_id,
                    "WARROOM",
                    f"🎉 GitHub PR #{pr_result['number']} 생성{dry}\n{pr_result.get('url')}",
                )
    else:
        closed = await _close_pr_if_exists(incident_id, github_client, github_repo)
        if closed:
            response["pr_closed"] = closed
            if closed.get("closed"):
                await asyncio.to_thread(
                    notifier.on_agent_update,
                    incident_id,
                    "WARROOM",
                    f"🧹 PR #{closed['number']} close + branch `{closed['branch']}` 삭제 완료",
                )
            elif "error" in closed:
                await asyncio.to_thread(
                    notifier.on_agent_update,
                    incident_id,
                    "WARROOM",
                    f"⚠️ PR #{closed['number']} cleanup 실패 — error_type={closed.get('error_type')}",
                )

    return JSONResponse(response)


async def _close_pr_if_exists(
    incident_id: str,
    client: GitHubClient,
    github_repo: str | None,
) -> dict | None:
    """반려 시 영속화된 PR 정보가 있으면 close + branch 삭제 (Phase 4.5).

    sync httpx 호출은 ``asyncio.to_thread`` 로 위임 (이벤트 루프 비점유).
    실패는 HTTP status 기반으로 분류해 응답 dict 에 ``error_type`` /
    ``status_code`` 를 surface 한다 — 운영자가 401 (토큰 회전 필요) /
    403 (권한) / 5xx (transient — 재시도 가치) 를 구분할 수 있게.
    """
    if not github_repo:
        return None

    repo = get_repository()
    pr_info = await repo.get_pr_info(incident_id)
    if not pr_info:
        return None
    pr_number, branch = pr_info

    try:
        await asyncio.to_thread(client.close_pr, github_repo, pr_number, branch)
    except GitHubAuthError as e:
        print(
            f"[WARROOM][cleanup] PR #{pr_number} close 실패 — "
            f"error_type=auth status_code={e.status_code} (토큰 회전 / 권한 점검 필요): {e}"
        )
        return {
            "number": pr_number,
            "branch": branch,
            "error": str(e),
            "error_type": "auth",
            "status_code": e.status_code,
        }
    except GitHubTransientError as e:
        retry_hint = f" retry_after={e.retry_after}s" if e.retry_after is not None else ""
        print(
            f"[WARROOM][cleanup] PR #{pr_number} close 실패 — "
            f"error_type=transient status_code={e.status_code}{retry_hint} (재시도 가치 있음): {e}"
        )
        result = {
            "number": pr_number,
            "branch": branch,
            "error": str(e),
            "error_type": "transient",
            "status_code": e.status_code,
        }
        if e.retry_after is not None:
            result["retry_after"] = e.retry_after
        return result
    except GitHubError as e:
        print(
            f"[WARROOM][cleanup] PR #{pr_number} close 실패 — "
            f"error_type=http status_code={e.status_code}: {e}"
        )
        return {
            "number": pr_number,
            "branch": branch,
            "error": str(e),
            "error_type": "http",
            "status_code": e.status_code,
        }
    except Exception as e:
        # 분류 안 된 예외 (네트워크 타임아웃 등 transport 레벨) — best-effort surface.
        print(f"[WARROOM][cleanup] PR #{pr_number} close 실패 — error_type=unknown: {e}")
        return {
            "number": pr_number,
            "branch": branch,
            "error": str(e),
            "error_type": "unknown",
        }
    return {"number": pr_number, "branch": branch, "closed": True}


def _open_pr(
    entry: dict,
    client: GitHubClient,
    github_repo: str | None,
) -> dict | None:
    """승인된 인시던트로 PR 을 만든다. GITHUB_REPO 미설정 시 skip."""
    if not github_repo:
        print("[WARROOM] GITHUB_REPO 미설정 — PR 생성 건너뜀")
        return None

    report_dict = entry.get("report")
    if not report_dict:
        print("[WARROOM] 리포트가 없어 PR 생성 건너뜀")
        return None

    category = report_dict.get("category", "code")
    if category != "code":
        print(f"[WARROOM] 카테고리 '{category}' — 코드 외 장애로 PR 생성 건너뜀")
        return {"skipped": True, "reason": f"category={category}"}

    created_at = report_dict["created_at"]
    if isinstance(created_at, str):
        created_at = datetime.fromisoformat(created_at)
    report = ResolutionReport(
        incident_id=entry["incident_id"],
        severity=Severity(report_dict["severity"]),
        category=IncidentCategory(report_dict.get("category", "code")),
        triage_summary=report_dict["triage_summary"] or "",
        root_cause=report_dict["root_cause"] or "",
        patch_suggestion=report_dict["patch_suggestion"] or "",
        post_mortem_draft=report_dict["post_mortem_draft"] or "",
        is_approved=True,
        created_at=created_at,
    )
    result = build_patch_pr(client, report, repo=github_repo)
    return {
        "url": result.pr_url,
        "branch": result.branch,
        "number": result.pr_number,
        "dry_run": result.dry_run,
    }
