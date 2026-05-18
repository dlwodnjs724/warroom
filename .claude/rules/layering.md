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

## 4a. 패키지 내부 layer — Protocol contract / clients / usecase 분리

`github` / `chatops` 처럼 단일 외부 도구를 어댑팅하는 패키지는 HTTP api 가 없으므로 gateway 의 3-layer 가 그대로 안 맞는다. 대신 **세 의미 단위** 로 분리:

| 의미 | 위치 | 책임 |
|---|---|---|
| **contract** | `base.py` | Protocol + error 계층 + result dataclass |
| **clients** | `clients/` 디렉터리 | Protocol 구현체 (실 호출 + dry-run 폴백 + factory) |
| **usecase** | 패키지 top-level (`pr_builder.py`, ...) | client 와 도메인 모델을 조합한 고수준 함수 |
| **infra helper** | 패키지 top-level (`patch.py`, `report.py`, ...) | 단일 도구 의존 helper (subprocess, render 등) |

**Why:** Protocol 구현체가 1개 (real) + 1개 (dry-run) 만 있어도 그룹핑 가치 있음. 새 transport 추가 시 (PAT 기반 client, status check API 등) 어디 놓을지 자명. usecase 와 평면에 섞이면 cold-context agent 가 "이 파일이 client 인지 usecase 인지" 매번 본문 읽어 판별해야 한다.

**예시 — github 패키지**:

```
packages/github/github/
├── base.py              # Protocol + GitHubError 계층 + PullRequestResult
├── clients/
│   ├── app.py           # GitHubAppClient
│   ├── dry_run.py       # DryRunGitHubClient
│   └── factory.py       # make_github_client (env → 적절 client)
├── pr_builder.py        # build_patch_pr usecase
├── patch.py             # verify_apply / apply_diff (git CLI)
└── report.py            # markdown render
```

## 4b. external adapter error 계층

외부 서비스 (GitHub / Slack / Sentry / ...) transport 의 HTTP 실패는 **운영 의미별** 로 분리된 예외로 surface 한다. raw `httpx.HTTPStatusError` 그대로 노출 금지 — 호출자가 status code 를 일일이 분기해야 하고, 재시도 vs 비재시도 결정이 보일러플레이트화.

**계층 패턴** (각 어댑터 패키지의 `base.py`):

```python
class GitHubError(Exception):
    """모든 GitHub transport 실패의 base — 호출자가 broad except 가능."""
    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code


class GitHubAuthError(GitHubError):
    """401 / 403 — 토큰 만료 또는 권한 부족. 재시도 무의미, 운영자 개입 필요."""


class GitHubTransientError(GitHubError):
    """5xx / 429 — GitHub 측 일시 장애 또는 rate-limit. 재시도 가치 있음."""
```

**분류 매트릭스**:

| HTTP status | 분류 | 호출자 분기 |
|---|---|---|
| 200~299 | (정상) | — |
| 401 / 403 | `<Adapter>AuthError` | 토큰 회전 / 권한 점검 |
| 404 | 도메인별 — `FileNotFoundError` 등 의미 있는 예외 또는 idempotent silent | 의미상 분기 |
| 422 | 도메인별 — 이미 처리된 멱등 케이스면 silent | — |
| 429 | `<Adapter>TransientError` | rate-limit 백오프 |
| 5xx | `<Adapter>TransientError` | 재시도 |

**구현 패턴** — 모든 transport primitive 는 단일 `_check(resp, context)` helper 만 거친다:

```python
def _check(resp: httpx.Response, context: str) -> None:
    if 200 <= resp.status_code < 300:
        return
    raise _classify_status(resp.status_code, context)
```

bare `resp.raise_for_status()` 사용 금지 — 분류 우회되어 raw `HTTPStatusError` 가 호출자에 노출됨.

**Slack / Sentry / 그 외**: 같은 패턴. `SlackError` / `SlackAuthError` / `SlackTransientError`. naming consistency.

## 4c. composition root — env / factory 호출 단일 지점

services layer 는 `os.getenv(...)` / `make_X()` 를 **직접 호출하지 않는다**. 모든 외부 어댑터 생성 + env credential 읽기는 **composition root** (`gateway/dependencies.py`) 한 곳에 집중.

```python
# ❌ NO — services 가 env + factory 직접
async def handle_decision(...):
    repo_target = os.getenv("GITHUB_REPO")
    client = make_github_client()
    ...

# ✅ YES — composition root 가 wiring, services 는 의존성을 인자로 받음
# gateway/dependencies.py
_github_client: GitHubClient | None = None

def get_github_client() -> GitHubClient:
    global _github_client
    if _github_client is None:
        _github_client = make_github_client()
    return _github_client

def get_github_repo() -> str | None:
    return os.getenv("GITHUB_REPO")

def reset_github_client() -> None:   # 테스트 격리용
    global _github_client
    _github_client = None

# gateway/services/decisions.py
async def handle_decision(...):
    client = get_github_client()
    github_repo = get_github_repo()
    ...
```

**Why:**
- env credential 위치 단일 지점 — 새 secret 추가 시 한 곳만 보면 됨
- 테스트 격리 — `reset_X` + `monkeypatch.setattr("gateway.dependencies.X", ...)` 한 패턴
- composition root 가 lifecycle 책임 — 비싼 factory 호출 (PEM 파일 read 등) 1회만
- service 가 env 모름 = 테스트하기 쉬움

**check during review**: services/* 파일 안에 `os.getenv` / `make_X` 직접 호출이 있으면 거절. composition root 로 옮긴다.

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
| Protocol 구현체 디렉터리 | `clients/` (`github/clients/app.py`, `chatops/clients/slack.py`) |
| factory 함수 | `make_X()` (`make_notifier`, `make_github_client`) |
| 캐시된 singleton getter | `get_X()` (`get_repository`, `get_github_client`) |
| 테스트 격리용 reset | `reset_X()` (`reset_repository`, `reset_github_client`) |
| 외부 adapter error 계층 | `<Tool>Error` → `<Tool>AuthError` / `<Tool>TransientError` |
