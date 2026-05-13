"""애플리케이션 표준 시간.

`datetime.now()` / `datetime.utcnow()` 직접 호출 금지 — 모든 시간은 이 모듈을 거친다.

- 기본 TZ: UTC
- env `APP_TZ` 로 override (예: `Asia/Seoul`). 테스트 격리/지역화 운영을 위한 안전판
- 테스트에서 시간 mock 은 `monkeypatch.setattr("common.clock.now", ...)` 한 줄로 끝
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

APP_TZ = ZoneInfo(os.getenv("APP_TZ", "UTC"))


def now() -> datetime:
    """현재 시각 (tz-aware, APP_TZ)."""
    return datetime.now(APP_TZ)
