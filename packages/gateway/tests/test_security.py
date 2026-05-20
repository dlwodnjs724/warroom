"""Webhook 서명 검증 단위 테스트.

secret 은 함수 인자 — env 우회 검증이 가능해 ``monkeypatch.setenv`` 가 필요
없다 (#11 으로 인자화된 services/security 와 정합). replay 윈도우 검사의
시간 기준은 ``gateway.services.security._now`` 를 monkeypatch.
"""

import hashlib
import hmac
from datetime import datetime
from zoneinfo import ZoneInfo

from gateway.services.security import (
    verify_datadog_token,
    verify_sentry_signature,
    verify_slack_signature,
)


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


class TestSentrySignature:
    def test_skips_when_secret_missing(self):
        assert verify_sentry_signature(b"{}", None, None) is True
        assert verify_sentry_signature(b"{}", "anything", None) is True
        assert verify_sentry_signature(b"{}", "anything", "") is True

    def test_accepts_valid_signature(self):
        body = b'{"a": 1}'
        sig = _sign("topsecret", body)
        assert verify_sentry_signature(body, sig, "topsecret") is True

    def test_rejects_invalid_signature(self):
        assert verify_sentry_signature(b'{"a": 1}', "deadbeef", "topsecret") is False

    def test_rejects_missing_signature_when_secret_set(self):
        assert verify_sentry_signature(b"{}", None, "topsecret") is False

    def test_rejects_when_body_tampered(self):
        sig = _sign("topsecret", b'{"a": 1}')
        assert verify_sentry_signature(b'{"a": 2}', sig, "topsecret") is False


class TestDatadogToken:
    def test_skips_when_token_missing(self):
        assert verify_datadog_token(None, None) is True
        assert verify_datadog_token("anything", None) is True
        assert verify_datadog_token("anything", "") is True

    def test_accepts_matching_token(self):
        assert verify_datadog_token("shared-token-xyz", "shared-token-xyz") is True

    def test_rejects_wrong_token(self):
        assert verify_datadog_token("wrong", "shared-token-xyz") is False

    def test_rejects_missing_token_when_expected(self):
        assert verify_datadog_token(None, "shared-token-xyz") is False


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
        monkeypatch.setattr("gateway.services.security._now", lambda: frozen)

    def test_skips_when_secret_missing(self):
        assert verify_slack_signature(self.BODY, None, None, None) is True
        assert verify_slack_signature(self.BODY, "v0=anything", "0", None) is True
        assert verify_slack_signature(self.BODY, "v0=anything", "0", "") is True

    def test_accepts_valid_signature(self, monkeypatch):
        self._frozen(monkeypatch)
        ts = str(self.NOW_EPOCH)
        sig = _slack_sign(self.SECRET, self.BODY, ts)
        assert verify_slack_signature(self.BODY, sig, ts, self.SECRET) is True

    def test_rejects_invalid_signature(self, monkeypatch):
        self._frozen(monkeypatch)
        ts = str(self.NOW_EPOCH)
        assert verify_slack_signature(self.BODY, "v0=deadbeef", ts, self.SECRET) is False

    def test_rejects_missing_signature_when_secret_set(self, monkeypatch):
        self._frozen(monkeypatch)
        assert verify_slack_signature(self.BODY, None, "0", self.SECRET) is False

    def test_rejects_missing_timestamp(self, monkeypatch):
        self._frozen(monkeypatch)
        assert verify_slack_signature(self.BODY, "v0=abc", None, self.SECRET) is False

    def test_rejects_replay_outside_window(self, monkeypatch):
        self._frozen(monkeypatch)
        old_ts = str(self.NOW_EPOCH - 60 * 6)  # 6분 전
        sig = _slack_sign(self.SECRET, self.BODY, old_ts)
        assert verify_slack_signature(self.BODY, sig, old_ts, self.SECRET) is False

    def test_replay_boundary_accepts_exactly_300_seconds(self, monkeypatch):
        """``abs(current - ts) > 300`` (strict gt) → 300s 정확히 일치 시 accept."""
        self._frozen(monkeypatch)
        boundary_ts = str(self.NOW_EPOCH - 300)
        sig = _slack_sign(self.SECRET, self.BODY, boundary_ts)
        assert verify_slack_signature(self.BODY, sig, boundary_ts, self.SECRET) is True

    def test_replay_boundary_rejects_301_seconds(self, monkeypatch):
        self._frozen(monkeypatch)
        outside_ts = str(self.NOW_EPOCH - 301)
        sig = _slack_sign(self.SECRET, self.BODY, outside_ts)
        assert verify_slack_signature(self.BODY, sig, outside_ts, self.SECRET) is False

    def test_rejects_non_integer_timestamp(self, monkeypatch):
        self._frozen(monkeypatch)
        assert verify_slack_signature(self.BODY, "v0=abc", "not-a-number", self.SECRET) is False

    def test_rejects_when_body_tampered(self, monkeypatch):
        self._frozen(monkeypatch)
        ts = str(self.NOW_EPOCH)
        sig = _slack_sign(self.SECRET, self.BODY, ts)
        assert verify_slack_signature(b"payload=tampered", sig, ts, self.SECRET) is False
