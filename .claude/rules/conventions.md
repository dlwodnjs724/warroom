# Engineering Conventions

CLAUDE.md 보조 — 코드 읽어도 안 보이고, 안 지키면 런타임/CI 에서 터지는 규칙들.

---

## DB 레이어

### 1. Store 호출은 항상 `await`

`gateway.store.IncidentStore` 의 모든 메서드는 async. 동기 호출 금지 — 이벤트 루프 점유로 1초 룰 위반.

```python
# ❌ NO
store.add(event)
entry = store.get(incident_id)

# ✅ YES
await store.add(event)
entry = await store.get(incident_id)
```

`run_pipeline` (CrewAI sync) 을 async 컨텍스트에서 호출할 때:

```python
# ✅ to_thread 로 워커 스레드 위임 (이벤트 루프 비점유)
report = await asyncio.to_thread(run_pipeline, event, notifier)
```

### 2. async session 에서 relationship 직접 접근 금지

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

### 3. 스키마 초기화 정책

| 백엔드 | 자동 셋업 | 비고 |
|---|---|---|
| SQLite (`sqlite+aiosqlite://...`) | ✅ `lifespan` / `demo.py` 가 `init_schema()` 호출 | dev/test 편의 |
| MySQL (`mysql+aiomysql://...`) | ❌ 자동 안 함 | **반드시 `uv run alembic upgrade head` 선행** |

분기는 `gateway.db.session.is_sqlite_backend()` 한 곳. 새 DB 코드 추가 시 우회하지 말 것.

### 4. 스키마 변경 = Alembic revision

모델 수정 후:
```bash
uv run alembic revision --autogenerate -m "<message>"
uv run alembic upgrade head   # MySQL 환경에서 검증
```

`metadata.create_all` 만 의지하지 말 것 — MySQL 운영에서는 Alembic 만이 진실의 소스.

---

## 테스트

### 1. 디렉터리 레이아웃

| 위치 | 용도 |
|---|---|
| `packages/<pkg>/tests/` | 단일 패키지 단위 테스트 (store, parser, security, ...) |
| `tests/` | 크로스-패키지 통합 (gateway + orchestrator + chatops + github) |

새 단위 테스트는 패키지 안에 둘 것. `tests/` 는 통합 한정.

### 2. `__init__.py` 절대 금지 (테스트 디렉터리)

pytest 가 `tests/test_X` 와 `packages/<pkg>/tests/test_X` 를 같은 모듈명으로 인식 → import 충돌.

```bash
# 새 테스트 디렉터리 만든 후 확인:
find tests packages/*/tests -name __init__.py   # 비어 있어야 한다
```

### 3. DB 격리

root `conftest.py` 의 autouse `_isolate_db` 가 자동으로:
- `DATABASE_URL=sqlite+aiosqlite:///:memory:` 강제
- 매 테스트 시작/종료에 `reset_engine()` + `reset_store()`

→ 테스트 코드에서 DATABASE_URL 직접 만지지 말 것.

스키마 생성:
- **gateway 통합 테스트**: `with TestClient(app) as c:` 로 lifespan 트리거 — 자동
- **store 단위 테스트**: `schema` fixture (`packages/gateway/tests/conftest.py`) 명시 주입

### 4. 패키지 재구성 후 editable 재설치

`packages/<pkg>/pyproject.toml` 이나 `__init__.py` 가 바뀌면 import 가 stale 해질 수 있다:

```bash
uv pip install -e packages/<pkg>
```

`ImportError: cannot import name 'X'` 가 코드상 분명히 있는데 뜨면 90% 이 케이스.

---

## Async / FastAPI

### 1. 1초 룰 (Gateway)

`/webhook/*` 는 무거운 작업 직접 수행 금지. 반드시 `BackgroundTasks` 위임 + 즉시 `202 Accepted` 반환.

### 2. CrewAI 는 sync — `asyncio.to_thread` 로 감싸기

`orchestrator.runner.run_pipeline` 은 sync 함수. async endpoint/lifespan/demo 어디서든 직접 `await` 불가.

```python
report = await asyncio.to_thread(run_pipeline, event, notifier)
```

### 3. Lifespan 에서 외부 IO 는 최소화

스키마 자동 셋업처럼 빠른 작업만. LLM 워밍업/네트워크 헬스체크 같은 건 첫 요청에 미루는 것이 안전.
