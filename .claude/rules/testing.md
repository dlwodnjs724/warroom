# 테스트 컨벤션

## 1. 디렉터리 레이아웃

| 위치 | 용도 |
|---|---|
| `packages/<pkg>/tests/` | 단일 패키지 단위 테스트 (repository, monitor, security, ...) |
| `tests/` | 크로스-패키지 통합 (gateway + orchestrator + chatops + github) |

새 단위 테스트는 패키지 안에. `tests/` 는 통합 한정.

실행은 항상 repo 루트에서 한 번:

```bash
uv run pytest
```

## 2. `__init__.py` 절대 금지 (테스트 디렉터리)

pytest 가 `tests/test_X` 와 `packages/<pkg>/tests/test_X` 를 같은 모듈명으로 인식 → import 충돌.

```bash
# 새 테스트 디렉터리 만든 후 확인:
find tests packages/*/tests -name __init__.py   # 비어 있어야 한다
```

## 3. `pytest-asyncio` mode = `auto`

`pyproject.toml` 에 `asyncio_mode = "auto"` 설정. 따라서 `async def test_...` 에 `@pytest.mark.asyncio` **붙이지 말 것** — auto 모드가 자동 인식.

```python
# ✅ YES (mark 없음)
async def test_repo_add():
    repo = get_repository()
    await repo.add(event)

# ❌ NO — 중복
@pytest.mark.asyncio   # 불필요
async def test_repo_add(): ...
```

## 4. DB 격리 — autouse fixture

root `conftest.py` 의 `_isolate_db` 가 자동으로:

- `DATABASE_URL=sqlite+aiosqlite:///:memory:` 강제
- 매 테스트 시작/종료에 `reset_engine()` + `reset_repository()`

→ 테스트 코드에서 `DATABASE_URL` 직접 만지지 말 것.

스키마 생성:

- **gateway 통합 테스트**: `with TestClient(app) as c:` 로 lifespan 트리거 — 자동
- **repository 단위 테스트**: `schema` fixture (`packages/gateway/tests/conftest.py`) 명시 주입

## 5. LLM 호출은 `MOCK_PIPELINE=true` 로 봉인

테스트/CI 에서 실 LLM 호출은 절대 금지 (비용 + rate limit + 비결정성).

- root `conftest.py` 또는 개별 테스트가 `monkeypatch.setenv("MOCK_PIPELINE", "true")` 보장
- `orchestrator.runner.run_pipeline` 은 `MOCK_PIPELINE=true` 면 mock 응답만 반환
- 실 LLM 통합 검증은 별도 수동 단계 (`demo.py` 또는 `serve.py` + 실제 webhook)

## 6. 환경변수는 `monkeypatch.setenv`

`os.environ["X"] = ...` 직접 set 금지 — 다른 테스트로 누수. 항상:

```python
def test_something(monkeypatch):
    monkeypatch.setenv("SENTRY_CLIENT_SECRET", "test-secret")
    ...
```

테스트 종료 시 자동 복원.

## 7. 패키지 재구성 후 editable 재설치

`packages/<pkg>/pyproject.toml` 이나 패키지 구조가 바뀌면 import 가 stale 할 수 있다:

```bash
uv pip install -e packages/<pkg>
```

`ImportError: cannot import name 'X'` 가 코드상 분명히 있는데 뜨면 90% 이 케이스.
