# Layering 컨벤션

코드가 커지고 여러 작업자/에이전트가 같이 만지더라도 모듈 경계가 흐려지지 않게 하기 위한 룰. hexagonal 강박은 아니다 — 단지 응집도와 변경 격리를 위한 layered 적용.

## 1. Layer 구조 (`packages/gateway` 기준)

```
api/              ← presentation. FastAPI router. HTTP 진입점.
services/         ← application. usecase 한 단위 = 함수 한 개. repository + 외부 어댑터 조합.
infrastructure/   ← DB / monitor 어댑터 / 외부 IO.
common (별도 pkg) ← 도메인 모델 (IncidentEvent, ResolutionReport, ...).
```

각 패키지 (chatops, orchestrator, github) 도 같은 원칙 — 패키지 내부에서 layer 분리.

## 2. Import 방향 — 위에서 아래로만

| from ↓ \ to →  | api | services | infrastructure | common |
|---|---|---|---|---|
| **api**            | ✅ | ✅ | ❌ | ✅ |
| **services**       | ❌ | ✅ | ✅ | ✅ |
| **infrastructure** | ❌ | ❌ | ✅ | ✅ |

**핵심 룰: `api` 는 `infrastructure` 를 직접 import 하지 않는다.** 단순 read 도 service 경유.

```python
# ❌ NO — api 가 repository 직접 호출
@router.get("/incidents")
async def list_incidents():
    return await get_repository().list_all()

# ✅ YES — service 한 줄이라도 거친다
@router.get("/incidents")
async def list_incidents():
    return await incidents_service.list_all()

# services/incidents.py
async def list_all() -> list[dict]:
    return await get_repository().list_all()
```

**Why:** repository 가 dict → 도메인 객체로 반환 형태를 바꾸거나, Pydantic 응답 모델 / 권한 체크 / 캐싱이 추가될 때 — 수정 지점이 1곳 (service) 으로 집중. anemic 보일러플레이트 비용 < 변경 격리 이득. multi-agent 환경에서는 모듈 boundary = 작업 boundary 가 되므로 더 중요.

**How to apply:** 새 API endpoint 추가 시 무조건 `services/` 함수 1개와 짝지어 작성. 같은 도메인의 service 들은 같은 파일에 모아 (예: `services/incidents.py` 에 query, `services/decisions.py` 에 승인/반려).

## 3. service 간 호출 — 같은 layer 안에서는 OK

`services/decisions.py` 가 `services/pipeline.py` 의 함수를 호출하는 건 OK. 단 양방향 의존이 생기면 한쪽이 helper 로 떨어져야 한다 (순환 import 방지).

## 4. infrastructure 내부 — 어댑터별 응집

`infrastructure/` 의 하위 디렉터리는 외부 의존 단위로 묶는다:

- `infrastructure/db/` — DB 관련 전부 (models, session, repository)
- `infrastructure/monitors/` — Sentry/Datadog 같은 모니터링 도구 어댑터 (parser + webhook 서명 검증)

새 외부 의존이 추가되면 (예: Redis 캐시) `infrastructure/cache/` 처럼 어댑터별 디렉터리 1개 추가.

## 5. 새 모듈 추가 시 체크리스트

- [ ] 이 모듈은 어느 layer 인가? (api / services / infrastructure)
- [ ] import 하는 모듈이 더 아래 layer 또는 동일 layer 인가? (위 표 위배 시 거절)
- [ ] 한 가지 책임으로 명명 가능한가? **`utils.py` / `helpers.py` / `common.py` 금지** — 떠다니는 코드의 쓰레기통이 된다. 책임 명확한 이름을 못 짓겠으면 잘못된 분리.
- [ ] 같은 도메인의 기존 service 와 합쳐도 되는가? 1줄짜리 파일을 새로 만들기 전에 기존 위치 검토.

## 6. naming 일관성

| 개념 | 명칭 |
|---|---|
| 영속화 추상 | `Repository` (NOT `Store`, `Dao`, `Manager`) |
| usecase 함수 | 동사로 시작. `ingest_event`, `handle_decision`, `run_incident_pipeline` |
| 외부 어댑터 | 도구 이름. `monitors/sentry.py`, `monitors/datadog.py` |
| factory 함수 | `make_X()` (`make_notifier`, `make_github_client`) |
| 캐시된 singleton getter | `get_X()` (`get_repository`) |
