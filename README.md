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
| LLM | Anthropic Claude Sonnet 4.6 |
| Package Manager | uv workspace (Python 3.13) |

## Quick Start

```bash
# 1. 의존성 설치
uv sync --all-packages
uv pip install -e packages/common -e packages/gateway -e packages/orchestrator -e packages/chatops

# 2. 환경변수 설정
cp .env.example .env
# .env에 ANTHROPIC_API_KEY 입력
# 프로토타입 테스트는 MOCK_PIPELINE=true

# 3-a. 서버 실행
uv run serve.py

# 3-b. CLI 실행 (데모)
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
└── chatops/      # Notifier 인터페이스 (Console / Slack)
```

## 확장 포인트

| 항목 | 방법 |
|------|------|
| Datadog 웹훅 | `gateway/parsers/datadog.py` 추가 |
| Slack 알림 | `chatops/slack.py` 추가 (`Notifier` 구현) |
| 실제 Sentry API | `orchestrator/tools/sentry.py` TODO 교체 |
| 실제 GitHub API | `orchestrator/tools/github.py` TODO 교체 |
| RDB 저장 | `gateway/store.py` `IncidentStore` 교체 |
