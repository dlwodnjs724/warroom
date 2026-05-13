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

## 2. MySQL `DATETIME` 은 TZ 미저장

write 시 tz-aware → read 시 naive 로 돌아온다. 비교하려면 명시 변환:

```python
stored = await s.get(Incident, id).received_at   # naive
stored_aware = stored.replace(tzinfo=APP_TZ)     # 비교 가능

# 또는 비교 직전 양쪽 normalize
elapsed = now().replace(tzinfo=None) - stored
```

장기적으로 `TIMESTAMP` 컬럼 또는 connection time_zone 강제 (`SET time_zone='+00:00'`) 로 정리하는 게 안전 — 운영 셋업 단계에서 결정.

## 3. JSON 직렬화는 `.isoformat()`

```python
{"received_at": event.received_at.isoformat()}   # 2026-05-13T12:00:00+00:00
```

`strftime` 으로 직접 포맷 박는 것 지양 (TZ suffix 누락 위험). 파일명 등 사람용은 예외.

## 4. ruff DTZ 가 강제

`pyproject.toml` 의 `[tool.ruff.lint.extend-select] = ["DTZ"]` 가 naive datetime 호출을 모두 차단. `common/clock.py` 만 예외 (정의 자체가 helper 라).
