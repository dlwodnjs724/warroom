"""Warroom 전 패키지의 logger 설정 단일 진입점.

배경: print prefix 패턴 (``[WARROOM][slack]`` / ``[pr_builder][ORPHAN]`` 등) 이
누적되어 logger 도입 ROI 분기점 통과. 모든 `print` 호출은 ``logging.getLogger(__name__)``
+ ``logger.info`` / ``warning`` / ``error`` 로 대체된다.

호출 패턴:
    import logging
    logger = logging.getLogger(__name__)
    ...
    logger.warning("PR #%s close 실패 — %s", pr_number, e)

진입점 (lifespan / demo.py) 에서 ``configure_logging()`` 1회 호출.

환경변수:
    WARROOM_LOG_LEVEL   DEBUG / INFO / WARNING / ERROR / CRITICAL (기본 INFO)
    WARROOM_LOG_FORMAT  human (기본) / json
        - human: ``2026-06-03T15:00 INFO warroom.gateway.services.decisions: ...``
        - json:  ``{"ts":"...","level":"...","logger":"...","msg":"..."}``
"""

import json
import logging
import os

from .clock import now


class JsonFormatter(logging.Formatter):
    """structured JSON line — observability 파이프라인 / Sentry 친화."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": now().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        # exception 정보가 있으면 포함
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


_configured: bool = False


def configure_logging() -> None:
    """프로세스 lifecycle 동안 1회만 적용 (idempotent).

    환경변수를 읽어 root logger 의 handler / level / formatter 를 세팅.
    이미 설정된 상태면 no-op — 테스트 환경에서 매번 reset 하려면
    ``reset_logging()`` 으로 명시 해제.
    """
    global _configured
    if _configured:
        return

    level_name = os.getenv("WARROOM_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    fmt_name = os.getenv("WARROOM_LOG_FORMAT", "human").lower()

    handler = logging.StreamHandler()
    if fmt_name == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )

    root = logging.getLogger()
    # 기존 handlers 제거 — pytest / uvicorn 등이 추가한 중복 출력 방지.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(handler)
    root.setLevel(level)
    _configured = True


def reset_logging() -> None:
    """테스트 격리용 — 다음 ``configure_logging()`` 호출이 다시 적용된다."""
    global _configured
    _configured = False
