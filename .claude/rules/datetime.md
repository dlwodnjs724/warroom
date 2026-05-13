# Datetime / Clock 컨벤션

이 프로젝트는 시간 비교/저장이 본질 (MTTD, MTTR, dedupe TTL, SLA). 단일 시간 소스로 통일하지 않으면 비교 버그 / 테스트 mock 불가 / TZ 누락 사고가 누적된다.

## 1. 시간은 무조건 `common.clock.now()`

```python
# ✅ YES
from common.clock import now

received_at: datetime = field(default_factory=now)
elapsed = now() - event.received_at

# ❌ NO — 룰 위반, ruff DTZ 가 차단
datetime.now()                       # naive, local TZ
datetime.utcnow()                    # deprecated (Python 3.12+)
datetime.now(timezone.utc)           # 작동은 하지만 호출 지점마다 박는 건 금지
```

`common.clock` 은 `APP_TZ` (`env APP_TZ`, 기본 `UTC`) 를 단일 소스로 사용. 시간 mocking 도 한 줄:

```python
def test_dedupe_ttl(monkeypatch):
    monkeypatch.setattr("common.clock.now", lambda: datetime(2026, 5, 13, 12, 0, tzinfo=ZoneInfo("UTC")))
```

## 2. 저장은 무조건 tz-aware UTC

스키마는 모든 timestamp 컬럼을 `DateTime(timezone=True)` 로 선언한다 — SQLAlchemy 가 UTC 보존을 보장:

```python
created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- **SQLite (aiosqlite)**: ISO TEXT 로 저장, TZ 보존
- **MySQL (asyncmy)**: SQLAlchemy 가 `TIMESTAMP` 로 매핑 → 내부 UTC + 세션 TZ 로 변환 반환

MySQL 서버/세션 TZ 도 UTC 로 강제 (이중 안전망):
- `docker-compose.yml`: `command: --default-time-zone=+00:00`
- `DATABASE_URL`: `?init_command=SET%20time_zone%3D%27%2B00:00%27`

→ tz-aware datetime 으로 write, tz-aware datetime 으로 read. naive 가 어디서도 안 끼게 한다.

비교 코드는 그냥 빼면 됨:

```python
elapsed = now() - stored_event.received_at   # 양쪽 tz-aware UTC, 안전
```

호스트/컨테이너 TZ 는 무관 (`clock.now()` 가 항상 UTC). 그래도 관례상 `TZ=UTC` 로 통일 (로그/cron 정합성).

## 3. JSON 직렬화는 `.isoformat()`

```python
{"received_at": event.received_at.isoformat()}   # 2026-05-13T12:00:00+00:00
```

`strftime` 으로 직접 포맷 박는 것 지양 (TZ suffix 누락 위험). 파일명 등 사람용은 예외.

## 4. ruff DTZ 가 강제

`pyproject.toml` 의 `[tool.ruff.lint.extend-select] = ["DTZ"]` 가 naive datetime 호출을 모두 차단. `common/clock.py` 만 예외 (정의 자체가 helper 라).
