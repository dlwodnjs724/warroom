# Warroom

AI Agent 기반 서비스 장애 탐지 및 대응 자동화 시스템.

Sentry 등 모니터링 도구의 웹훅을 수신하여 멀티 에이전트 AI 파이프라인이 근본 원인을 분석하고 패치를 제안합니다. 개발자 승인(Human-in-the-Loop) 후에만 패치가 적용됩니다.

## Architecture

```
[Sentry Webhook]
      │
      ▼
┌─────────────┐  202 즉시 반환
│   Gateway   │
│  (FastAPI)  │
└──────┬──────┘
       │ BackgroundTask
       ▼
┌─────────────────────────────┐
│     Orchestrator (CrewAI)   │
│                             │
│  Triage Agent               │
│  └─ 심각도 분류, MTTD 추정  │
│                             │
│  Analyst Agent              │
│  ├─ Sentry 스택트레이스 조회 │
│  └─ 5 Whys / Fishbone 분석  │
│                             │
│  Fixer Agent                │
│  └─ 패치 코드 + 재발 방지   │
└──────────────┬──────────────┘
               ▼
        ┌─────────────┐
        │   ChatOps   │  Console / Slack
        └─────────────┘
               ▼
     awaiting_approval
               │
    approve / reject
```

## Stack

| 역할 | 기술 |
|------|------|
| Event Gateway | FastAPI + BackgroundTasks |
| AI Orchestration | CrewAI (Sequential Process) |
| LLM (PoC 기본) | Google Gemini 2.5 Flash / Flash-Lite (Free Tier) |
| LLM (운영 권장) | Anthropic Claude Sonnet 4.6 + Haiku 4.5 |
| Package Manager | uv workspace (Python 3.13) |

LLM provider는 환경변수로 전환 (`LLM_PROVIDER=gemini|anthropic|ollama`). Triage·Analyst·Fixer 에이전트별로 모델을 분리해 비용을 최적화한다.

## Quick Start

```bash
# 1. 의존성 설치
uv sync --all-packages
uv pip install -e packages/common -e packages/gateway -e packages/orchestrator -e packages/chatops -e packages/github

# 1-1. pre-commit hook 설치 (commit 시 ruff format/lint 자동 강제)
uv run pre-commit install

# 2. 환경변수 설정
cp .env.example .env
# 기본은 Gemini Free Tier — .env 에 GEMINI_API_KEY 입력
#   (https://aistudio.google.com/apikey 에서 발급, 무료)
# Anthropic 사용 시: LLM_PROVIDER=anthropic + ANTHROPIC_API_KEY 입력
# 비용 없이 흐름만 검증할 때: MOCK_PIPELINE=true

# 3. DB 셋업
#    - SQLite (로컬 빠른 체험)  : 별도 작업 불필요. 서버/데모 첫 실행 시 자동 스키마 생성
#    - MySQL  (dev/prod 권장)   : 컨테이너 띄우고 `alembic upgrade head` 필수
docker compose up -d
# .env 에 DATABASE_URL=mysql+aiomysql://warroom:warroom@localhost:3306/warroom 설정
uv run alembic upgrade head
# (Test/CI 는 in-memory SQLite — 별도 셋업 불필요)

# 4-a. 서버 실행
uv run serve.py

# 4-b. CLI 실행 (데모)
uv run demo.py
```

## API

```bash
# 웹훅 전송
curl -X POST http://localhost:8000/webhook/sentry \
  -H "Content-Type: application/json" \
  -d '{"data":{"issue":{"id":"sentry-001","title":"NullPointerException"}}}'

# 상태 확인
curl http://localhost:8000/incidents/sentry-001

# 승인 / 반려
curl -X POST http://localhost:8000/incidents/sentry-001/approve
curl -X POST http://localhost:8000/incidents/sentry-001/reject
```

## Package Structure

```
packages/
├── common/       # 공유 데이터 모델 (IncidentEvent, ResolutionReport)
├── gateway/      # FastAPI webhook 수신 + HITL 엔드포인트
├── orchestrator/ # CrewAI 에이전트 파이프라인
├── chatops/      # Notifier 인터페이스 (Console / Slack)
└── github/       # GitHub App 기반 PR 자동 생성
```

## 확장 포인트

| 항목 | 방법 |
|------|------|
| Datadog 웹훅 | `gateway/parsers/datadog.py` 추가 |
| Slack 알림 | `chatops/slack.py` 추가 (`Notifier` 구현) |
| 실제 Sentry API | `orchestrator/tools/sentry.py` TODO 교체 |
| GitHub PR 자동 생성 | `packages/github/` (App 미설정 시 dry-run) |
| 스키마 변경 | `alembic revision --autogenerate -m "..."` → `alembic upgrade head` |

## GitHub App 설정 (PR 자동 생성)

승인된 인시던트는 `incidents/<id>.md` 파일을 만든 새 브랜치를 push 하고
PR 을 open 한다. App credentials 가 없으면 dry-run 으로 `output/github_payloads.jsonl`
에 페이로드만 기록한다.

1. https://github.com/settings/apps/new 에서 GitHub App 생성
2. **Permissions** — Contents: Read & write, Pull requests: Write, Metadata: Read
3. App 을 **데모용 레포(예: `<you>/warroom-demo`)** 에 install
4. App 페이지에서 private key (`.pem`) 다운로드 → `.secrets/warroom-app.private-key.pem`
5. `.env` 에 추가:
   ```
   GITHUB_REPO=<owner>/warroom-demo
   GITHUB_APP_ID=<App ID>
   GITHUB_APP_PRIVATE_KEY_PATH=./.secrets/warroom-app.private-key.pem
   GITHUB_INSTALLATION_ID=<Installation ID>
   ```
   Installation ID 는 `https://github.com/settings/installations` 에서 확인.
