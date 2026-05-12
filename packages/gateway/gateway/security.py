"""Webhook 서명 검증.

각 모니터링 소스가 권장하는 방식으로 페이로드 무결성을 확인한다. 운영
환경에서는 secret 미설정 시 거절해야 안전하지만, dev/테스트 편의를 위해
secret 이 비어 있으면 검증을 건너뛴다. 운영자는 startup 로그에서 경고를
확인해야 한다.

Sentry: HMAC-SHA256(raw_body, SENTRY_CLIENT_SECRET) → X-Sentry-Signature 헤더
Datadog: 표준 서명 헤더가 없으므로 공유 토큰 헤더(X-Warroom-Token)로 대체
"""
import hashlib
import hmac
import os


def verify_sentry_signature(body: bytes, signature: str | None) -> bool:
    secret = os.getenv("SENTRY_CLIENT_SECRET")
    if not secret:
        return True
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_datadog_token(token: str | None) -> bool:
    expected = os.getenv("WARROOM_DATADOG_TOKEN")
    if not expected:
        return True
    if not token:
        return False
    return hmac.compare_digest(expected, token)


def warn_if_secrets_missing() -> None:
    """Startup 시 호출 — secret 이 없으면 dev 모드임을 명시적으로 알린다."""
    missing = [
        name for name in ("SENTRY_CLIENT_SECRET", "WARROOM_DATADOG_TOKEN")
        if not os.getenv(name)
    ]
    if missing:
        print(
            f"[WARROOM] 경고: 다음 webhook 서명 검증이 비활성화되어 있습니다 — {', '.join(missing)}. "
            "운영 환경에서는 반드시 설정하세요."
        )
