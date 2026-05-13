# DB 컨벤션

코드 읽어도 안 보이고, 안 지키면 런타임에 터지는 DB 레이어 규칙.

## 1. Store 호출은 항상 `await`

`gateway.store.IncidentStore` 의 모든 메서드는 async. 동기 호출 금지 — 이벤트 루프 점유로 1초 룰 위반.

```python
# ❌ NO
store.add(event)
entry = store.get(incident_id)

# ✅ YES
await store.add(event)
entry = await store.get(incident_id)
```

## 2. async session 에서 relationship 직접 접근 금지

SQLAlchemy 2.0 async session 은 lazy load 가 막혀 있다. `obj.relation` 접근하면 `MissingGreenlet` 으로 터짐.

```python
# ❌ NO — 런타임 폭발
async with factory() as s:
    existing = await s.get(Incident, incident_id)
    old_report = existing.report   # lazy load → MissingGreenlet

# ✅ YES — 명시적 fetch
async with factory() as s:
    existing = await s.get(Incident, incident_id)
    old_report = await s.get(Report, incident_id)
```

또는 쿼리에서 `selectinload(Incident.report)` 로 eager load.

## 3. 스키마 초기화 정책

| 백엔드 | 자동 셋업 | 비고 |
|---|---|---|
| SQLite (`sqlite+aiosqlite://...`) | ✅ `lifespan` / `demo.py` 가 `init_schema()` 호출 | dev/test 편의 |
| MySQL (`mysql+aiomysql://...`) | ❌ 자동 안 함 | **반드시 `uv run alembic upgrade head` 선행** |

분기는 `gateway.db.session.is_sqlite_backend()` 한 곳. 새 DB 코드 추가 시 우회하지 말 것.

## 4. 스키마 변경 = Alembic revision

모델 수정 후:

```bash
uv run alembic revision --autogenerate -m "<message>"
uv run alembic upgrade head   # MySQL 환경에서 검증
```

`metadata.create_all` 만 의지하지 말 것 — MySQL 운영에서는 Alembic 만이 진실의 소스.
