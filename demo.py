"""
warroom 프로토타입 실행 진입점.

사용법:
    uv run demo.py

환경변수 (.env 참고):
    LLM_PROVIDER         gemini | anthropic | ollama (기본: gemini)
    GEMINI_API_KEY       provider=gemini 일 때 필수
    ANTHROPIC_API_KEY    provider=anthropic 일 때 필수
    MOCK_PIPELINE        true 이면 LLM 호출 없이 mock 응답 사용
    DATABASE_URL         dev/prod=MySQL, test=in-memory SQLite (기본: 로컬 sqlite 파일)
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

_USE_MOCK = os.getenv("MOCK_PIPELINE", "false").lower() == "true"

if not _USE_MOCK:
    from orchestrator.llm import required_api_key_env, selected_models

    api_key_env = required_api_key_env()
    if api_key_env and not os.getenv(api_key_env):
        print(f"[오류] {api_key_env} 환경변수가 설정되지 않았습니다.")
        print(f"  .env 파일에 {api_key_env}=... 를 추가하세요.")
        sys.exit(1)

    _models = selected_models()
    print(f"[WARROOM] LLM Provider: {_models['provider']}")
    print(f"  Triage : {_models['triage']}")
    print(f"  Analyst: {_models['analyst']}")
    print(f"  Fixer  : {_models['fixer']}")

from chatops.factory import make_notifier
from common.models import IncidentStatus
from gateway.db.session import init_schema, is_sqlite_backend
from gateway.parsers import sentry as sentry_parser
from gateway.store import get_store
from orchestrator.runner import run_pipeline


MOCK_SENTRY_PAYLOAD = {
    "data": {
        "issue": {
            "id": "sentry-4821",
            "title": "NullPointerException at app/gateways/stripe.py",
            "culprit": "stripe.charge",
            "level": "error",
            "project": {"name": "payment-service"},
        }
    },
    "action": "created",
}


def save_report_json(report) -> Path:
    """결과 리포트를 JSON 파일로도 보관 (개발자 가독용)."""
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    report_path = output_dir / f"{report.incident_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    data = {
        "incident_id": report.incident_id,
        "severity": report.severity.value,
        "category": report.category.value,
        "triage_summary": report.triage_summary,
        "root_cause": report.root_cause,
        "patch_suggestion": report.patch_suggestion,
        "post_mortem_draft": report.post_mortem_draft,
        "is_approved": report.is_approved,
        "created_at": report.created_at.isoformat(),
    }
    report_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return report_path


def human_approval() -> bool:
    """Human-in-the-Loop: 개발자 승인/반려."""
    print("\n" + "=" * 60)
    print("  패치 제안을 승인하시겠습니까?")
    print("  [y] 승인 — 포스트모템 저장")
    print("  [n] 반려 — 재검토 필요 표시")
    print("=" * 60)

    while True:
        answer = input("  선택 (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  y 또는 n 을 입력하세요.")


async def main():
    if is_sqlite_backend():
        await init_schema()
    store = get_store()
    notifier = make_notifier()

    print("\n[WARROOM] 프로토타입 시작")
    print("[WARROOM] Mock Sentry 페이로드로 파이프라인을 실행합니다.\n")

    # 1. 파싱
    event = sentry_parser.parse(MOCK_SENTRY_PAYLOAD)
    await store.add(event)
    notifier.on_incident_received(event)

    # 2. 에이전트 파이프라인 (sync) — to_thread 로 이벤트 루프 비점유
    try:
        await store.update_status(event.incident_id, IncidentStatus.ANALYZING)
        report = await asyncio.to_thread(run_pipeline, event, notifier)
        await store.save_report(event.incident_id, report)
        await store.update_status(event.incident_id, IncidentStatus.AWAITING_APPROVAL)
    except Exception as e:
        await store.update_status(event.incident_id, IncidentStatus.FAILED)
        print(f"\n[오류] 파이프라인 실행 실패: {e}")
        sys.exit(1)

    # 3. Human-in-the-Loop
    approved = human_approval()
    report.is_approved = approved
    final_status = IncidentStatus.APPROVED if approved else IncidentStatus.REJECTED
    await store.update_status(event.incident_id, final_status, is_approved=approved)

    # 4. JSON 산출물 (가독용 보조 출력)
    path = save_report_json(report)

    if approved:
        print(f"\n[WARROOM] 승인 완료. 리포트 저장: {path}")
        _open_github_pr(report)
    else:
        print(f"\n[WARROOM] 반려 처리. 리포트 저장: {path}")
        print("[WARROOM] (TODO: 재분석 요청 또는 수동 대응)")


def _open_github_pr(report) -> None:
    """승인된 리포트로 GitHub PR 생성. GITHUB_REPO 미설정 시 skip."""
    repo = os.getenv("GITHUB_REPO")
    if not repo:
        print("[WARROOM] GITHUB_REPO 미설정 — PR 생성 건너뜀")
        return
    from github.factory import make_github_client

    client = make_github_client()
    result = client.create_patch_pr(report, repo=repo)
    tag = "(dry-run)" if result.dry_run else ""
    print(f"[WARROOM] PR 생성 {tag}: {result.pr_url}")


if __name__ == "__main__":
    asyncio.run(main())
