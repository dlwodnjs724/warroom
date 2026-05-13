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
# 최소: GEMINI_API_KEY (또는 MOCK_PIPELINE=true 로 LLM 없이 흐름 검증)
# 외부 서비스 (Slack / Sentry / GitHub App / ngrok) 전체 셋업은 docs/setup.md 참고

# 3. DB 셋업
#    - SQLite (로컬 빠른 체험)  : 별도 작업 불필요. 서버/데모 첫 실행 시 자동 스키마 생성
#    - MySQL  (dev/prod 권장)   : 컨테이너 띄우고 `alembic upgrade head` 필수
docker compose up -d
# .env 에 DATABASE_URL=mysql+asyncmy://warroom:warroom@localhost:3306/warroom 설정
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
| 새 모니터링 소스 | `gateway/infrastructure/monitors/<name>.py` 어댑터 + `gateway/api/webhooks.py` 라우터 추가 |
| 실제 Sentry/GitHub API tool | `orchestrator/tools/{sentry,github}.py` mock 교체 (Phase 6.2) |
| 멀티 레포 매핑 | `gateway/services/decisions.py` `_open_pr` (현재 단일 `GITHUB_REPO`) |
| 스키마 변경 | `alembic revision --autogenerate -m "..."` → `alembic upgrade head` |

세부 layer 룰: [`.claude/rules/layering.md`](./.claude/rules/layering.md). 현재 구조: [`docs/architecture.md`](./docs/architecture.md).

## 외부 서비스 셋업

Slack / Sentry / ngrok / GitHub App / LLM provider 전체 단계별 가이드 — [`docs/setup.md`](./docs/setup.md).

App credentials 가 없어도 dry-run 으로 동작 (Slack=콘솔, GitHub=`output/github_payloads.jsonl`, LLM=`MOCK_PIPELINE=true`).
