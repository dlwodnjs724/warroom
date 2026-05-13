"""Webhook 서명 검증 단위 테스트."""

import hashlib
import hmac

from gateway.infrastructure.monitors.security import verify_datadog_token, verify_sentry_signature


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class TestSentrySignature:
    def test_skips_when_secret_missing(self, monkeypatch):
        monkeypatch.delenv("SENTRY_CLIENT_SECRET", raising=False)
        assert verify_sentry_signature(b"{}", None) is True
        assert verify_sentry_signature(b"{}", "anything") is True

    def test_accepts_valid_signature(self, monkeypatch):
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "topsecret")
        body = b'{"a": 1}'
        sig = _sign("topsecret", body)
        assert verify_sentry_signature(body, sig) is True

    def test_rejects_invalid_signature(self, monkeypatch):
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "topsecret")
        assert verify_sentry_signature(b'{"a": 1}', "deadbeef") is False

    def test_rejects_missing_signature_when_secret_set(self, monkeypatch):
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "topsecret")
        assert verify_sentry_signature(b"{}", None) is False

    def test_rejects_when_body_tampered(self, monkeypatch):
        monkeypatch.setenv("SENTRY_CLIENT_SECRET", "topsecret")
        sig = _sign("topsecret", b'{"a": 1}')
        assert verify_sentry_signature(b'{"a": 2}', sig) is False


class TestDatadogToken:
    def test_skips_when_token_missing(self, monkeypatch):
        monkeypatch.delenv("WARROOM_DATADOG_TOKEN", raising=False)
        assert verify_datadog_token(None) is True
        assert verify_datadog_token("anything") is True

    def test_accepts_matching_token(self, monkeypatch):
        monkeypatch.setenv("WARROOM_DATADOG_TOKEN", "shared-token-xyz")
        assert verify_datadog_token("shared-token-xyz") is True

    def test_rejects_wrong_token(self, monkeypatch):
        monkeypatch.setenv("WARROOM_DATADOG_TOKEN", "shared-token-xyz")
        assert verify_datadog_token("wrong") is False

    def test_rejects_missing_token_when_expected(self, monkeypatch):
        monkeypatch.setenv("WARROOM_DATADOG_TOKEN", "shared-token-xyz")
        assert verify_datadog_token(None) is False
