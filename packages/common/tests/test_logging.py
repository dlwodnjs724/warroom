"""common.logging.configure_logging + JsonFormatter 단위 테스트."""

import json
import logging

from common.logging import JsonFormatter, configure_logging, reset_logging


class TestConfigureLogging:
    def test_default_level_info(self, monkeypatch):
        monkeypatch.delenv("WARROOM_LOG_LEVEL", raising=False)
        monkeypatch.delenv("WARROOM_LOG_FORMAT", raising=False)
        reset_logging()
        configure_logging()
        assert logging.getLogger().level == logging.INFO

    def test_debug_level_from_env(self, monkeypatch):
        monkeypatch.setenv("WARROOM_LOG_LEVEL", "DEBUG")
        reset_logging()
        configure_logging()
        assert logging.getLogger().level == logging.DEBUG

    def test_invalid_level_falls_back_to_info(self, monkeypatch):
        monkeypatch.setenv("WARROOM_LOG_LEVEL", "BOGUS")
        reset_logging()
        configure_logging()
        # getattr fallback → INFO
        assert logging.getLogger().level == logging.INFO

    def test_is_idempotent(self, monkeypatch):
        """두 번째 호출은 재설정 안 함 (handlers 중복 방지)."""
        reset_logging()
        configure_logging()
        handlers_first = list(logging.getLogger().handlers)
        configure_logging()
        handlers_second = list(logging.getLogger().handlers)
        assert handlers_first == handlers_second

    def test_reset_allows_reconfiguration(self, monkeypatch):
        reset_logging()
        monkeypatch.setenv("WARROOM_LOG_LEVEL", "WARNING")
        configure_logging()
        assert logging.getLogger().level == logging.WARNING

        reset_logging()
        monkeypatch.setenv("WARROOM_LOG_LEVEL", "ERROR")
        configure_logging()
        assert logging.getLogger().level == logging.ERROR


class TestJsonFormatter:
    def _record(self, msg: str = "hello", level: int = logging.INFO) -> logging.LogRecord:
        return logging.LogRecord(
            name="warroom.test",
            level=level,
            pathname=__file__,
            lineno=10,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_format_produces_valid_json(self):
        fmt = JsonFormatter()
        result = fmt.format(self._record())
        payload = json.loads(result)
        assert payload["msg"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "warroom.test"
        assert "ts" in payload

    def test_format_includes_exception_when_present(self):
        import sys

        fmt = JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                name="warroom.test",
                level=logging.ERROR,
                pathname=__file__,
                lineno=10,
                msg="oops",
                args=(),
                exc_info=sys.exc_info(),
            )
        result = fmt.format(record)
        payload = json.loads(result)
        assert "exc_info" in payload
        assert "ValueError: boom" in payload["exc_info"]

    def test_format_args_interpolation(self):
        """logger.info("count=%d", 42) → JSON 의 msg 가 "count=42"."""
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="warroom.test",
            level=logging.INFO,
            pathname=__file__,
            lineno=10,
            msg="count=%d",
            args=(42,),
            exc_info=None,
        )
        payload = json.loads(fmt.format(record))
        assert payload["msg"] == "count=42"
