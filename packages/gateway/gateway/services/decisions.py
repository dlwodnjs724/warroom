"""Human-in-the-Loop 승인/반려 결정 + 승인 시 GitHub PR 트리거."""

import os
from datetime import datetime

from common.models import IncidentCategory, IncidentStatus, ResolutionReport, Severity
from fastapi import HTTPException
from fastapi.responses import JSONResponse

from gateway.infrastructure.db.repository import get_repository


async def handle_decision(incident_id: str, approved: bool) -> JSONResponse:
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
    await repo.update_status(incident_id, status, is_approved=approved)

    action = "승인" if approved else "반려"
    print(f"[WARROOM] 인시던트 {incident_id} {action} 처리 완료")

    response: dict[str, object] = {
        "incident_id": incident_id,
        "status": status,
        "action": action,
    }
    if approved:
        pr_result = _open_pr(entry)
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
    else:
        closed = await _close_pr_if_exists(incident_id)
        if closed:
            response["pr_closed"] = closed

    return JSONResponse(response)


async def _close_pr_if_exists(incident_id: str) -> dict | None:
    """반려 시 영속화된 PR 정보가 있으면 close + branch 삭제 (Phase 4.5)."""
    repo_target = os.getenv("GITHUB_REPO")
    if not repo_target:
        return None

    repo = get_repository()
    pr_info = await repo.get_pr_info(incident_id)
    if not pr_info:
        return None
    pr_number, branch = pr_info

    from github.factory import make_github_client

    client = make_github_client()
    try:
        client.close_pr(repo_target, pr_number, branch)
    except Exception as e:
        print(f"[WARROOM] PR cleanup 실패 (best-effort): {e}")
        return {"number": pr_number, "branch": branch, "error": str(e)}
    return {"number": pr_number, "branch": branch, "closed": True}


def _open_pr(entry: dict) -> dict | None:
    """승인된 인시던트로 PR 을 만든다. GITHUB_REPO 미설정 시 skip."""
    repo = os.getenv("GITHUB_REPO")
    if not repo:
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

    from github.factory import make_github_client

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
    client = make_github_client()
    result = client.create_patch_pr(report, repo=repo)
    return {
        "url": result.pr_url,
        "branch": result.branch,
        "number": result.pr_number,
        "dry_run": result.dry_run,
    }
