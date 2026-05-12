"""
Event Gateway — FastAPI webhook receiver.

엔드포인트:
  POST /webhook/sentry          Sentry 웹훅 수신
  POST /webhook/datadog         Datadog 웹훅 수신
  GET  /incidents               처리된 인시던트 목록
  POST /incidents/{id}/approve  패치 제안 승인
  POST /incidents/{id}/reject   패치 제안 반려
"""
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import JSONResponse

load_dotenv()

from datetime import datetime

from common.models import IncidentEvent, IncidentStatus, ResolutionReport, Severity
from gateway.parsers import datadog as datadog_parser
from gateway.parsers import sentry as sentry_parser
from gateway.store import incident_store

# orchestrator는 import 지연 (LLM 초기화 비용)
def _run_pipeline(event: IncidentEvent) -> None:
    from chatops.factory import make_notifier
    from orchestrator.runner import run_pipeline

    notifier = make_notifier()
    try:
        incident_store.update_status(event.incident_id, IncidentStatus.ANALYZING)
        report = run_pipeline(event, notifier)
        incident_store.save_report(event.incident_id, report)
        incident_store.update_status(event.incident_id, IncidentStatus.AWAITING_APPROVAL)
    except Exception as e:
        print(f"[ERROR] 파이프라인 실패 ({event.incident_id}): {e}")
        incident_store.update_status(event.incident_id, IncidentStatus.FAILED)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[WARROOM] Gateway 시작")
    yield
    print("[WARROOM] Gateway 종료")


app = FastAPI(title="Warroom Event Gateway", lifespan=lifespan)


@app.post("/webhook/sentry", status_code=202)
async def webhook_sentry(payload: dict[str, Any], background_tasks: BackgroundTasks):
    """Sentry 웹훅 수신 — 즉시 202 반환 후 백그라운드에서 파이프라인 실행."""
    return _ingest(sentry_parser.parse(payload), background_tasks)


@app.post("/webhook/datadog", status_code=202)
async def webhook_datadog(payload: dict[str, Any], background_tasks: BackgroundTasks):
    """Datadog 웹훅 수신 — Monitor/Incident 페이로드 모두 처리."""
    return _ingest(datadog_parser.parse(payload), background_tasks)


def _ingest(event: IncidentEvent, background_tasks: BackgroundTasks) -> dict:
    """파싱된 IncidentEvent 를 dedupe 처리 후 파이프라인에 흘려보낸다."""
    existing = incident_store.get(event.incident_id)
    if existing:
        count = incident_store.increment_dupe_count(event.incident_id)
        print(f"[WARROOM] 중복 수신 — {event.incident_id} (총 {count}건). 기존 파이프라인 유지.")
        return {
            "incident_id": event.incident_id,
            "status": "duplicate",
            "dupe_count": count,
        }
    incident_store.add(event)
    background_tasks.add_task(_run_pipeline, event)
    return {"incident_id": event.incident_id, "status": "accepted"}


@app.get("/incidents")
async def list_incidents():
    """처리된 인시던트 목록 조회."""
    return incident_store.list_all()


@app.get("/incidents/{incident_id}")
async def get_incident(incident_id: str):
    entry = incident_store.get(incident_id)
    if not entry:
        raise HTTPException(status_code=404, detail="인시던트를 찾을 수 없습니다.")
    return entry


@app.post("/incidents/{incident_id}/approve")
async def approve_incident(incident_id: str):
    """패치 제안 승인 (Human-in-the-Loop)."""
    return _handle_decision(incident_id, approved=True)


@app.post("/incidents/{incident_id}/reject")
async def reject_incident(incident_id: str):
    """패치 제안 반려 (Human-in-the-Loop)."""
    return _handle_decision(incident_id, approved=False)


def _handle_decision(incident_id: str, approved: bool) -> JSONResponse:
    entry = incident_store.get(incident_id)
    if not entry:
        raise HTTPException(status_code=404, detail="인시던트를 찾을 수 없습니다.")
    if entry["status"] != IncidentStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=409,
            detail=f"승인 대기 상태가 아닙니다. 현재 상태: {entry['status']}",
        )

    status = IncidentStatus.APPROVED if approved else IncidentStatus.REJECTED
    incident_store.update_status(incident_id, status, is_approved=approved)

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

    from github.factory import make_github_client

    report = ResolutionReport(
        incident_id=entry["incident_id"],
        severity=Severity(report_dict["severity"]),
        triage_summary=report_dict["triage_summary"] or "",
        root_cause=report_dict["root_cause"] or "",
        patch_suggestion=report_dict["patch_suggestion"] or "",
        post_mortem_draft=report_dict["post_mortem_draft"] or "",
        is_approved=True,
        created_at=datetime.fromisoformat(report_dict["created_at"]),
    )
    client = make_github_client()
    result = client.create_patch_pr(report, repo=repo)
    return {
        "url": result.pr_url,
        "branch": result.branch,
        "number": result.pr_number,
        "dry_run": result.dry_run,
    }
