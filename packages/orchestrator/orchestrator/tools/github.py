from crewai.tools import tool


@tool("GitHub Source Lookup")
def github_source_lookup(file_path: str) -> str:
    """
    GitHub에서 특정 파일의 최근 변경 이력과 소스코드를 조회합니다.
    실제 환경에서는 GitHub API를 호출하지만, 현재는 Mock 데이터를 반환합니다.
    """
    # TODO: 실제 GitHub API 연동 시 여기를 교체
    return f"""
[GitHub] {file_path} — 최근 커밋 이력

커밋 abc1234 (2026-04-09 03:10 UTC) by dev-kim
  "feat: stripe client 초기화 방식 변경 (lazy loading 적용)"

변경 내용 (diff):
- self.client = stripe.Client(api_key=settings.STRIPE_KEY)
+ self.client = None  # lazy init

+ def _ensure_client(self):
+     if self.client is None:
+         self.client = stripe.Client(api_key=settings.STRIPE_KEY)

문제: charge() 메서드에서 _ensure_client() 호출 누락됨

현재 코드 (line 85-92):
    def charge(self, payment_method, amount):
        params = {{
            "amount": amount,
            "currency": "krw",
            "payment_method": payment_method,
        }}
        return self.client.charges.create(**params)  # ← client가 None일 수 있음
"""
