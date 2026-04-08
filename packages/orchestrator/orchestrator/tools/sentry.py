from crewai.tools import tool


@tool("Sentry Issue Lookup")
def sentry_issue_lookup(issue_id: str) -> str:
    """
    Sentry에서 특정 이슈의 상세 스택트레이스와 발생 컨텍스트를 조회합니다.
    실제 환경에서는 Sentry API를 호출하지만, 현재는 Mock 데이터를 반환합니다.
    """
    # TODO: 실제 Sentry API 연동 시 여기를 교체
    return f"""
[Sentry Issue #{issue_id}] Mock 데이터

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
