import os
import time

from chatops.base import Notifier
from common.models import IncidentCategory, IncidentEvent, ResolutionReport, Severity

# LLM 사용 여부: MOCK_PIPELINE=true 이면 mock 응답 사용
_USE_MOCK = os.getenv("MOCK_PIPELINE", "false").lower() == "true"


def run_pipeline(event: IncidentEvent, notifier: Notifier) -> ResolutionReport:
    notifier.on_agent_update("WARROOM", "에이전트 파이프라인 시작...")

    if _USE_MOCK:
        return _run_mock_pipeline(event, notifier)
    return _run_crew_pipeline(event, notifier)


def _run_mock_pipeline(event: IncidentEvent, notifier: Notifier) -> ResolutionReport:
    """LLM 없이 고정 응답으로 전체 플로우를 검증하는 mock 파이프라인."""

    notifier.on_agent_update("Triage Agent", "인시던트 심각도 분류 및 타임라인 재구성 중...")
    time.sleep(1)
    triage_output = f"""\
- 심각도: HIGH
- 카테고리: code
- 영향 서비스: payment-service
- 영향 사용자: 약 312명
- 최초 발생: 2026-04-09 03:10 UTC
- MTTD 추정: 약 2분 (03:12 UTC 알람 감지)
- 트리거 후보: 03:10 UTC 배포 (커밋 abc1234, Stripe 클라이언트 초기화 로직 변경)
- 요약: {event.title} 오류가 결제 서비스에서 배포 직후부터 급증. \
최근 1시간 내 847회 발생. lazy initialization 변경이 트리거로 추정됨.
- Sentry 이슈 ID: {event.incident_id}"""
    notifier.on_agent_update("Triage Agent", "초기 브리핑 완료.")

    notifier.on_agent_update("Analyst Agent", "Sentry 스택트레이스 조회 중...")
    time.sleep(1)
    notifier.on_agent_update("Analyst Agent", "GitHub 커밋 이력 조회 중...")
    time.sleep(1)
    notifier.on_agent_update("Analyst Agent", "5 Whys / Fishbone 분석 중...")
    time.sleep(1)
    analyst_output = """\
## 근본 원인 분석

### 5 Whys
1. **왜** NullPointerException이 발생했는가?
   → self.client가 None인 상태에서 charges.create()가 호출됨 [Confirmed]
2. **왜** self.client가 None이었는가?
   → charge() 메서드에서 _ensure_client() 호출이 누락됨 [Confirmed]
3. **왜** _ensure_client() 호출이 누락되었는가?
   → 커밋 abc1234에서 lazy init 패턴 도입 시 charge()를 수정하지 않음 [Confirmed]
4. **왜** 코드 리뷰에서 이를 발견하지 못했는가?
   → lazy init 적용 범위 체크리스트 부재 [Estimated]
5. **왜** 테스트가 이를 잡지 못했는가?
   → payment_method=None 케이스에 대한 단위 테스트 미존재 [Confirmed]

### Fishbone 분류
- **Technology**: charge()에서 _ensure_client() 미호출, 자동 None 검사 부재
- **Process**: lazy init 패턴 도입 시 영향 범위 체크리스트 없음, 코드 리뷰 누락
- **People**: 패턴 변경 시 호출부 전체 검토 관행 부재
- **Environment**: 배포 직후 트래픽이 몰리는 시간대와 겹침

### 직접 원인
charge() 메서드 내 _ensure_client() 호출 누락 [Confirmed]

### 근본 원인
lazy init 패턴 도입 시 영향받는 모든 호출부를 검토하는 프로세스 부재 [Estimated]

### 기여 요인
- payment_method=None 케이스 단위 테스트 부재 [Confirmed]
- 배포 후 결제 에러율 알람 임계값이 높아 MTTD 지연 [Estimated]"""
    notifier.on_agent_update("Analyst Agent", "근본 원인 분석 완료.")

    notifier.on_agent_update("Fixer Agent", "패치 코드 및 재발 방지 계획 수립 중...")
    time.sleep(1)
    patch_suggestion = """\
```python
# app/gateways/stripe.py — 즉시 패치

def charge(self, payment_method, amount):
    self._ensure_client()  # ← 누락된 호출 추가
    params = {
        "amount": amount,
        "currency": "krw",
        "payment_method": payment_method,
    }
    return self.client.charges.create(**params)
```"""

    postmortem = """\
## 포스트모템

### 단기 액션 (즉시~1주) — Prevention / Detection
| ID | 액션 | Defense Layer | KPI |
|----|------|--------------|-----|
| REM-001 | charge()에 _ensure_client() 추가 후 즉시 배포 | Prevention | 에러율 0으로 복귀 |
| REM-002 | payment_method=None 단위 테스트 추가 | Prevention | 테스트 통과율 100% |
| REM-003 | 결제 에러율 알람 임계값 0.5% → 0.1%로 강화 | Detection | MTTD < 1분 |

### 중기 액션 (1~4주) — Prevention / Response
| ID | 액션 | Defense Layer | KPI |
|----|------|--------------|-----|
| REM-004 | lazy init 패턴 도입 시 호출부 전체 검토 체크리스트 PR 템플릿에 추가 | Prevention | 체크리스트 적용률 100% |
| REM-005 | 결제 서비스 카나리 배포 도입 | Response | 카나리 배포 적용률 100% |

### 장기 액션 (1~3개월) — Recovery
| ID | 액션 | Defense Layer | KPI |
|----|------|--------------|-----|
| REM-006 | 결제 게이트웨이 자동 롤백 구현 | Recovery | MTTR < 5분 |
| REM-007 | lazy init 패턴 린터 룰 추가 | Prevention | 린터 경고 0건 |"""

    notifier.on_agent_update("Fixer Agent", "패치 코드 및 포스트모템 초안 완료.")

    report = ResolutionReport(
        incident_id=event.incident_id,
        severity=Severity.HIGH,
        category=IncidentCategory.CODE,
        triage_summary=triage_output,
        root_cause=analyst_output,
        patch_suggestion=patch_suggestion,
        post_mortem_draft=postmortem,
    )
    notifier.on_resolution_ready(report)
    return report


def _run_crew_pipeline(event: IncidentEvent, notifier: Notifier) -> ResolutionReport:
    """실제 LLM을 사용하는 CrewAI 파이프라인.

    Triage 단계 결과의 category 가 'code' 인 경우에만 Analyst/Fixer 까지
    진행한다. 그 외 카테고리는 코드 패치가 무의미하므로 분석 리포트만 남긴다.
    """
    from crewai import Crew, Process, Task

    from .agents import make_analyst_agent, make_fixer_agent, make_triage_agent

    triage_agent = make_triage_agent()
    payload_summary = (
        f"인시던트 ID: {event.incident_id}\n"
        f"소스: {event.source}\n"
        f"제목: {event.title}\n"
        f"원본 페이로드: {event.raw_payload}"
    )
    triage_task = Task(
        description=(
            f"다음 인시던트를 분석하고 초기 브리핑을 작성하세요.\n\n{payload_summary}\n\n"
            "출력 형식:\n"
            "- 심각도: [critical/high/medium/low]\n"
            "- 카테고리: [code/infra/external/operational]\n"
            "- 요약: (2-3문장으로 상황 설명)\n"
            "- Sentry 이슈 ID: (페이로드에서 추출, 없으면 'unknown')"
        ),
        expected_output="심각도, 카테고리, 상황 요약, Sentry 이슈 ID를 포함한 초기 브리핑",
        agent=triage_agent,
    )
    Crew(
        agents=[triage_agent],
        tasks=[triage_task],
        process=Process.sequential,
        verbose=True,
    ).kickoff()

    triage_output = triage_task.output.raw if triage_task.output else ""
    severity = _extract_severity(triage_output)
    category = _extract_category(triage_output)

    if category != IncidentCategory.CODE:
        notifier.on_agent_update(
            "WARROOM",
            f"카테고리 '{category.value}' — Analyst/Fixer 단계 생략, 운영 대응 권장",
        )
        report = _build_triage_only_report(event, triage_output, severity, category)
        notifier.on_resolution_ready(report)
        return report

    analyst_agent = make_analyst_agent()
    fixer_agent = make_fixer_agent()
    analyst_task = Task(
        description=(
            f"이전 Triage 결과:\n{triage_output}\n\n"
            "위 브리핑을 바탕으로 근본 원인을 분석하세요.\n"
            "1. Sentry Issue Lookup 툴로 스택트레이스를 확인하세요.\n"
            "2. GitHub Source Lookup 툴로 관련 파일의 최근 변경사항을 확인하세요.\n"
            "3. 수집한 증거를 바탕으로 근본 원인을 명확히 서술하세요."
        ),
        expected_output="증거 기반의 근본 원인 분석",
        agent=analyst_agent,
    )
    fixer_task = Task(
        description=(
            "Analyst Agent의 근본 원인 분석을 바탕으로 다음을 작성하세요.\n"
            "1. 즉시 적용 가능한 패치 코드 (Python 코드 블록으로)\n"
            "2. 포스트모템 초안\n\n"
            "⚠️ 이 패치는 제안일 뿐이며, 개발자 승인 없이 자동으로 적용되지 않습니다."
        ),
        expected_output="패치 코드 스니펫과 포스트모템 초안",
        agent=fixer_agent,
        context=[analyst_task],
    )
    Crew(
        agents=[analyst_agent, fixer_agent],
        tasks=[analyst_task, fixer_task],
        process=Process.sequential,
        verbose=True,
    ).kickoff()

    analyst_output = analyst_task.output.raw if analyst_task.output else ""
    fixer_output = fixer_task.output.raw if fixer_task.output else ""
    patch, postmortem = _split_fixer_output(fixer_output)

    report = ResolutionReport(
        incident_id=event.incident_id,
        severity=severity,
        category=category,
        triage_summary=triage_output,
        root_cause=analyst_output,
        patch_suggestion=patch,
        post_mortem_draft=postmortem,
    )
    notifier.on_resolution_ready(report)
    return report


def _build_triage_only_report(
    event: IncidentEvent,
    triage_output: str,
    severity: Severity,
    category: IncidentCategory,
) -> ResolutionReport:
    """category != code 케이스 — Analyst/Fixer 없이 Triage 만으로 리포트 구성."""
    return ResolutionReport(
        incident_id=event.incident_id,
        severity=severity,
        category=category,
        triage_summary=triage_output,
        root_cause="(코드 외 카테고리 — 추가 분석 단계 생략)",
        patch_suggestion="",
        post_mortem_draft=(f"카테고리: {category.value}. 코드 패치 대신 운영 대응이 필요합니다."),
    )


def _extract_severity(triage_output: str) -> Severity:
    lower = triage_output.lower()
    for level in ("critical", "high", "medium", "low"):
        if level in lower:
            return Severity(level)
    return Severity.MEDIUM


def _extract_category(triage_output: str) -> IncidentCategory:
    """Triage 출력에서 카테고리 추출. '카테고리:' 라인 우선, 없으면 키워드 매칭."""
    import re

    m = re.search(r"카테고리\s*[:：]\s*(\w+)", triage_output, re.IGNORECASE)
    if m:
        try:
            return IncidentCategory(m.group(1).lower())
        except ValueError:
            pass
    # fallback: 본문에 명시적으로 카테고리가 단독으로 등장하면 채택
    lower = triage_output.lower()
    for cat in IncidentCategory:
        if f" {cat.value}" in f" {lower}" or f"\n{cat.value}" in f"\n{lower}":
            return cat
    return IncidentCategory.CODE  # 안전한 기본값: 패치 시도


def _split_fixer_output(fixer_output: str) -> tuple[str, str]:
    """패치 코드와 포스트모템을 분리. 단순 구분자 기반."""
    markers = ["포스트모템", "post-mortem", "postmortem", "## 포스트", "## Post"]
    for marker in markers:
        if marker.lower() in fixer_output.lower():
            idx = fixer_output.lower().find(marker.lower())
            return fixer_output[:idx].strip(), fixer_output[idx:].strip()
    # 구분자 없으면 전체를 patch_suggestion으로
    return fixer_output, "(포스트모템 초안이 패치 제안에 포함되어 있습니다)"
