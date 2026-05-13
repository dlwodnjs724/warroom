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
        if pr_result:
            response["pull_request"] = pr_result

    return JSONResponse(response)


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
