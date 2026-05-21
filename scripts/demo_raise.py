"""실 코드 레벨 장애 시연.

demo repo (dlwodnjs724/warroom-demo) 의 stripe.py + payment.py 패턴을 재현하여
NullPointerException (AttributeError) 을 의도적으로 발생시킨다.

흐름:
    Python 실행 → raise AttributeError → sentry-sdk 자동 capture
    → Sentry 서버에 issue 등록
    → Sentry alert rule 발사 (Internal Integration webhook)
    → ngrok → warroom gateway → 분석 → Slack → ✅/❌ → PR/rejection

실행:
    uv run python scripts/demo_raise.py
"""

import os

import sentry_sdk
from dotenv import load_dotenv

load_dotenv()

DSN = os.getenv("SENTRY_DSN")
if not DSN:
    raise RuntimeError("SENTRY_DSN 환경변수가 필요합니다. .env 에 추가하세요.")

sentry_sdk.init(
    dsn=DSN,
    traces_sample_rate=0.0,
    send_default_pii=False,
    release="warroom-demo@incident-trigger",
    environment="demo",
)


# ---- demo repo 의 app/gateways/stripe.py 와 동일 패턴 ----
class StripeGateway:
    """Stripe 결제 게이트웨이.

    커밋 abc1234 에서 lazy initialization 패턴으로 변경되었으나
    charge() 메서드에서 _ensure_client() 호출이 누락된 상태.
    """

    def __init__(self):
        self.client = None  # lazy init

    def _ensure_client(self):
        if self.client is None:
            self.client = object()  # 실 stripe.Client 대신 dummy

    def refund(self, charge_id):
        self._ensure_client()
        return self.client.refunds.create(charge=charge_id)

    def charge(self, payment_method, amount):
        # ⚠️ _ensure_client() 호출 누락 → self.client 가 None 인 상태
        params = {
            "amount": amount,
            "currency": "krw",
            "payment_method": payment_method,
        }
        return self.client.charges.create(**params)

    def capture(self, charge_id, amount):
        # ⚠️ _ensure_client() 호출 누락 (charge 와 동일 결함의 캡처 경로)
        params = {"amount_to_capture": amount}
        return self.client.captures.create(charge_id, **params)

    def void_charge(self, charge_id, reason="requested_by_customer"):
        # ⚠️ _ensure_client() 호출 누락 (void 경로 — 같은 결함의 세 번째 발현)
        return self.client.voids.create(charge_id, reason=reason)


# ---- demo repo 의 app/services/payment.py 와 동일 패턴 ----
class PaymentService:
    def __init__(self):
        self.gateway = StripeGateway()

    def process_payment(self, user, amount):
        self._validate_amount(amount)
        result = self.gateway.charge(user.payment_method, amount)
        return {"status": "ok", "charge_id": result.id}

    def capture_authorized(self, charge_id, amount):
        self._validate_amount(amount)
        result = self.gateway.capture(charge_id, amount)
        return {"status": "captured", "charge_id": result.id}

    def void_charge(self, charge_id):
        result = self.gateway.void_charge(charge_id)
        return {"status": "voided", "charge_id": result.id}

    @staticmethod
    def _validate_amount(amount):
        if amount <= 0:
            raise ValueError(f"invalid amount: {amount}")


class User:
    def __init__(self, user_id: str, payment_method=None):
        self.user_id = user_id
        self.payment_method = payment_method


# ---- 환율 캐시 만료 시나리오 ----
class CurrencyConverter:
    """다중 통화 결제용 환율 변환기.

    환율 캐시가 빈 dict 로 초기화되는 race condition 이 있어
    cache miss 시 0 으로 나누는 버그가 있다.
    """

    def __init__(self):
        self.rates = {}  # ⚠️ 캐시 lazy load 안 됨

    def convert(self, amount: int, from_ccy: str, to_ccy: str) -> float:
        # rate=0 이면 ZeroDivisionError → Sentry 새 fingerprint
        rate = self.rates.get(f"{from_ccy}-{to_ccy}", 0)
        return amount / rate


def main():
    """실 호출 시나리오: USD→KRW 환율 변환 (캐시 미초기화)."""
    converter = CurrencyConverter()

    print("[demo] 환율 변환 시도 — 100 USD → KRW")
    try:
        converter.convert(amount=100, from_ccy="USD", to_ccy="KRW")
    except Exception as e:
        print(f"[demo] 예외 발생 — {type(e).__name__}: {e}")
        sentry_sdk.capture_exception(e)
        sentry_sdk.flush(timeout=5)
        print("[demo] Sentry 로 capture 완료. issue 등록 + alert rule 발사 대기.")
        raise


if __name__ == "__main__":
    main()
