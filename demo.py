"""
warroom 프로토타입 실행 진입점.

사용법:
    uv run demo.py

환경변수:
    ANTHROPIC_API_KEY  (필수)
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

if not os.getenv("ANTHROPIC_API_KEY"):
    print("[오류] ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
    print("  .env 파일에 ANTHROPIC_API_KEY=sk-ant-... 를 추가하세요.")
    sys.exit(1)

from gateway.parsers import sentry as sentry_parser
from orchestrator.runner import run_pipeline
from chatops.console import ConsoleNotifier


# Mock Sentry webhook 페이로드
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


def save_report(report) -> Path:
    """결과 리포트를 JSON 파일로 저장."""
    # TODO: RDB 저장으로 교체
    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)

    report_path = output_dir / f"{report.incident_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    data = {
        "incident_id": report.incident_id,
        "severity": report.severity.value,
        "triage_summary": report.triage_summary,
        "root_cause": report.root_cause,
        "patch_suggestion": report.patch_suggestion,
        "post_mortem_draft": report.post_mortem_draft,
        "is_approved": report.is_approved,
        "created_at": report.created_at.isoformat(),
    }
    report_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return report_path


def human_approval(report) -> bool:
    """Human-in-the-Loop: 개발자 승인/반려."""
    print("\n" + "="*60)
    print("  패치 제안을 승인하시겠습니까?")
    print("  [y] 승인 — 포스트모템 저장")
    print("  [n] 반려 — 재검토 필요 표시")
    print("="*60)

    while True:
        answer = input("  선택 (y/n): ").strip().lower()
        if answer in ("y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("  y 또는 n 을 입력하세요.")


def main():
    notifier = ConsoleNotifier()

    print("\n[WARROOM] 프로토타입 시작")
    print("[WARROOM] Mock Sentry 페이로드로 파이프라인을 실행합니다.\n")

    # 1. 파싱
    event = sentry_parser.parse(MOCK_SENTRY_PAYLOAD)
    notifier.on_incident_received(event)

    # 2. 에이전트 파이프라인
    try:
        report = run_pipeline(event, notifier)
    except Exception as e:
        print(f"\n[오류] 파이프라인 실행 실패: {e}")
        sys.exit(1)

    # 3. Human-in-the-Loop
    approved = human_approval(report)
    report.is_approved = approved

    # 4. 저장
    path = save_report(report)

    if approved:
        print(f"\n[WARROOM] 승인 완료. 리포트 저장: {path}")
        print("[WARROOM] (TODO: Jira 티켓 생성 확장 포인트)")
    else:
        print(f"\n[WARROOM] 반려 처리. 리포트 저장: {path}")
        print("[WARROOM] (TODO: 재분석 요청 또는 수동 대응)")


if __name__ == "__main__":
    main()
