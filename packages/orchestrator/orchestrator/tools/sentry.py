"""Analyst Agent 의 Sentry Issue Lookup tool.

``SENTRY_AUTH_TOKEN`` 이 설정되면 Sentry REST API 호출 (Internal Integration
Bearer auth). 미설정 / 호출 실패 시 mock 응답으로 자동 fallback — LLM 컨텍스트
안에서 tool 호출이 절대 fail 하지 않게 보호 (재시도 / 우회 prompt 비용 회피).

룰 § 4b (external adapter error 계층) 면제: 이 모듈은 LLM tool 표면이라
caller 가 분기할 의미 있는 예외가 없다 — 모든 비정상은 mock 텍스트로 surface.
"""

import logging
import os

import httpx
from crewai.tools import tool

logger = logging.getLogger(__name__)

_SENTRY_API = "https://sentry.io/api/0"
_TIMEOUT = 10.0


def _mock_response(issue_id: str) -> str:
    """token 없거나 호출 실패 시 fallback. demo 흐름 (mock 파이프라인) 과 동일 텍스트."""
    return f"""
[Sentry Issue #{issue_id}] Mock 데이터 (SENTRY_AUTH_TOKEN 미설정 또는 호출 실패)

Exception: NullPointerException
  File "app/services/payment.py", line 142, in process_payment
    result = gateway.charge(user.payment_method)
  File "app/gateways/stripe.py", line 89, in charge
    return self.client.charges.create(**params)

최근 1시간 발생 횟수: 847회
첫 발생: 2026-04-09 03:12:00 UTC
영향받은 사용자: 312명

추가 컨텍스트:
- user.payment_method 가 None 인 케이스에서만 발생
- 배포 직후(04:09 03:10 UTC) 부터 급증
- 관련 커밋: abc1234 (stripe client 초기화 로직 변경)
"""


def _format_issue(issue: dict, event: dict | None) -> str:
    """Sentry API 응답을 LLM 이 읽기 좋은 단일 문자열로."""
    title = issue.get("title") or issue.get("metadata", {}).get("title") or "(no title)"
    culprit = issue.get("culprit") or "(unknown)"
    first_seen = issue.get("firstSeen") or "(unknown)"
    last_seen = issue.get("lastSeen") or "(unknown)"
    count = issue.get("count") or "?"
    user_count = issue.get("userCount") or "?"
    permalink = issue.get("permalink") or ""

    lines = [
        f"[Sentry Issue #{issue.get('id')}] {title}",
        "",
        f"Culprit: {culprit}",
        f"발생 횟수 (total): {count} / 영향 사용자: {user_count}",
        f"첫 발생: {first_seen} / 마지막: {last_seen}",
    ]
    if permalink:
        lines.append(f"Sentry URL: {permalink}")

    if event:
        # 가장 최근 event 의 stacktrace — frames 추출
        entries = event.get("entries") or []
        for entry in entries:
            if entry.get("type") == "exception":
                values = entry.get("data", {}).get("values") or []
                for val in values:
                    exc_type = val.get("type") or "Exception"
                    exc_value = val.get("value") or ""
                    lines.extend(["", f"Exception: {exc_type}: {exc_value}"])
                    frames = (val.get("stacktrace") or {}).get("frames") or []
                    # in-app frames 우선, 없으면 전체
                    in_app = [f for f in frames if f.get("inApp")] or frames
                    # 최근 호출이 마지막. 역순으로 5개.
                    for f in reversed(in_app[-5:]):
                        filename = f.get("filename") or f.get("module") or "?"
                        function = f.get("function") or "?"
                        lineno = f.get("lineNo") or f.get("lineno") or "?"
                        lines.append(f'  File "{filename}", line {lineno}, in {function}')
                break
        # 추가 메타: release / environment (Sentry 의 정규 위치는 tags)
        tags = {t.get("key"): t.get("value") for t in (event.get("tags") or [])}
        release = tags.get("release")
        environment = tags.get("environment")
        if release or environment:
            lines.append("")
            if release:
                lines.append(f"Release: {release}")
            if environment:
                lines.append(f"Environment: {environment}")

    return "\n".join(lines)


@tool("Sentry Issue Lookup")
def sentry_issue_lookup(issue_id: str) -> str:
    """Sentry 에서 특정 이슈의 상세 스택트레이스 + 발생 컨텍스트 조회.

    실제 호출:
        GET /issues/{id}/                — 메타 (title, count, userCount, ...)
        GET /issues/{id}/events/latest/  — 가장 최근 event 의 stacktrace
    """
    token = os.getenv("SENTRY_AUTH_TOKEN")
    if not token:
        return _mock_response(issue_id)

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    try:
        with httpx.Client(timeout=_TIMEOUT) as c:
            issue_resp = c.get(f"{_SENTRY_API}/issues/{issue_id}/", headers=headers)
            issue_resp.raise_for_status()
            issue = issue_resp.json()

            event: dict | None = None
            try:
                event_resp = c.get(f"{_SENTRY_API}/issues/{issue_id}/events/latest/", headers=headers)
                event_resp.raise_for_status()
                event = event_resp.json()
            except Exception as e:
                # event 누락은 치명적이지 않음 — 메타만으로도 분석 가능.
                logger.warning("%s event lookup 실패: %s", issue_id, e)
    except Exception as e:
        logger.warning("%s lookup 실패 — mock 폴백: %s", issue_id, e)
        return _mock_response(issue_id)

    return _format_issue(issue, event)
