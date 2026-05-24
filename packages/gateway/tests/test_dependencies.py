"""composition root (`gateway.dependencies`) 단위 테스트."""

from gateway.dependencies import init_sentry, reset_sentry


class TestInitSentry:
    """Warroom self-monitoring init (dogfooding).

    실 ``sentry_sdk.init`` 은 dummy DSN 으로도 transport 생성 + 시작 이벤트
    flush 시도로 인해 테스트 종료 시 hang 위험이 있다 → ``sentry_sdk.init`` 자체를
    monkeypatch 해 호출 여부만 검증한다.
    """

    def test_returns_false_when_dsn_missing(self, monkeypatch):
        monkeypatch.delenv("SENTRY_DSN", raising=False)
        reset_sentry()
        assert init_sentry() is False

    def test_returns_true_when_dsn_present(self, monkeypatch):
        monkeypatch.setenv("SENTRY_DSN", "https://x@x.sentry.io/1")
        called: dict[str, int] = {"count": 0}

        def fake_init(**kwargs):
            called["count"] += 1

        monkeypatch.setattr("gateway.dependencies.sentry_sdk.init", fake_init)
        reset_sentry()
        assert init_sentry() is True
        assert called["count"] == 1

    def test_is_idempotent(self, monkeypatch):
        """두 번째 호출은 sentry_sdk.init 을 재호출하지 않고 True 만 반환."""
        monkeypatch.setenv("SENTRY_DSN", "https://x@x.sentry.io/1")
        called: dict[str, int] = {"count": 0}

        def fake_init(**kwargs):
            called["count"] += 1

        monkeypatch.setattr("gateway.dependencies.sentry_sdk.init", fake_init)
        reset_sentry()
        assert init_sentry() is True
        assert init_sentry() is True
        assert called["count"] == 1  # 두 번째는 skip
