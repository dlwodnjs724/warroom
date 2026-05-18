"""Webhook 서명 검증 단위 테스트."""

import hashlib
import hmac
from datetime import datetime
from zoneinfo import ZoneInfo

from gateway.infrastructure.monitors.security import (
    verify_datadog_token,
    verify_sentry_signature,
    verify_slack_signature,
)


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


def _slack_sign(secret: str, body: bytes, ts: str) -> str:
    base = f"v0:{ts}:".encode() + body
    return "v0=" + hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()


class TestSlackSignature:
    SECRET = "slack-signing-secret"
    BODY = b"payload=%7B%22type%22%3A%22block_actions%22%7D"
    NOW_EPOCH = 1_700_000_000

    @staticmethod
    def _frozen(monkeypatch, epoch: int = NOW_EPOCH) -> None:
        """``common.clock.now`` 를 고정 — verify_slack_signature 의 replay 윈도우 검사 결정성 확보."""
        frozen = datetime.fromtimestamp(epoch, tz=ZoneInfo("UTC"))
        monkeypatch.setattr("gateway.infrastructure.monitors.security._now", lambda: frozen)

    def test_skips_when_secret_missing(self, monkeypatch):
        monkeypatch.delenv("SLACK_SIGNING_SECRET", raising=False)
        assert verify_slack_signature(self.BODY, None, None) is True
        assert verify_slack_signature(self.BODY, "v0=anything", "0") is True

    def test_accepts_valid_signature(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self.SECRET)
        self._frozen(monkeypatch)
        ts = str(self.NOW_EPOCH)
        sig = _slack_sign(self.SECRET, self.BODY, ts)
        assert verify_slack_signature(self.BODY, sig, ts) is True

    def test_rejects_invalid_signature(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self.SECRET)
        self._frozen(monkeypatch)
        ts = str(self.NOW_EPOCH)
        assert verify_slack_signature(self.BODY, "v0=deadbeef", ts) is False

    def test_rejects_missing_signature_when_secret_set(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self.SECRET)
        self._frozen(monkeypatch)
        assert verify_slack_signature(self.BODY, None, "0") is False

    def test_rejects_missing_timestamp(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self.SECRET)
        self._frozen(monkeypatch)
        assert verify_slack_signature(self.BODY, "v0=abc", None) is False

    def test_rejects_replay_outside_window(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self.SECRET)
        self._frozen(monkeypatch)
        old_ts = str(self.NOW_EPOCH - 60 * 6)  # 6분 전
        sig = _slack_sign(self.SECRET, self.BODY, old_ts)
        assert verify_slack_signature(self.BODY, sig, old_ts) is False

    def test_replay_boundary_accepts_exactly_300_seconds(self, monkeypatch):
        """``abs(current - ts) > 300`` (strict gt) → 300s 정확히 일치 시 accept."""
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self.SECRET)
        self._frozen(monkeypatch)
        boundary_ts = str(self.NOW_EPOCH - 300)
        sig = _slack_sign(self.SECRET, self.BODY, boundary_ts)
        assert verify_slack_signature(self.BODY, sig, boundary_ts) is True

    def test_replay_boundary_rejects_301_seconds(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self.SECRET)
        self._frozen(monkeypatch)
        outside_ts = str(self.NOW_EPOCH - 301)
        sig = _slack_sign(self.SECRET, self.BODY, outside_ts)
        assert verify_slack_signature(self.BODY, sig, outside_ts) is False

    def test_rejects_non_integer_timestamp(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self.SECRET)
        self._frozen(monkeypatch)
        assert verify_slack_signature(self.BODY, "v0=abc", "not-a-number") is False

    def test_rejects_when_body_tampered(self, monkeypatch):
        monkeypatch.setenv("SLACK_SIGNING_SECRET", self.SECRET)
        self._frozen(monkeypatch)
        ts = str(self.NOW_EPOCH)
        sig = _slack_sign(self.SECRET, self.BODY, ts)
        assert verify_slack_signature(b"payload=tampered", sig, ts) is False
