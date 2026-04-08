# Architecture

## 개요

Sentry 등 모니터링 툴의 웹훅을 시작점으로, 3개의 AI 에이전트가 순차 협업하여 근본 원인 분석 → 패치 제안까지 자동화. 최종 패치는 개발자 승인 후에만 적용된다.

```
[Sentry webhook]
      │
      ▼
┌─────────────┐    202 즉시 반환
│   Gateway   │ ──────────────────→ (webhook sender)
│  (FastAPI)  │
└──────┬──────┘
       │ BackgroundTask
       ▼
┌─────────────────────────────────┐
│        Orchestrator (CrewAI)    │
│                                 │
│  Triage Agent                   │
│    └─ 심각도 분류, 초기 브리핑   │
│         │                       │
│  Analyst Agent                  │
│    ├─ Tool: Sentry Lookup       │
│    └─ Tool: GitHub Lookup       │
│         │ 근본 원인 분석         │
│  Fixer Agent                    │
│    └─ 패치 코드 + 포스트모템     │
└──────────────┬──────────────────┘
               │
               ▼
        ┌─────────────┐
        │   ChatOps   │  ConsoleNotifier (→ Slack 확장)
        └─────────────┘
               │
               ▼
     awaiting_approval 상태
               │
    ┌──────────┴──────────┐
    │                     │
 approve               reject
    │
 (TODO: Jira 티켓)
```

## 패키지 구조

```
packages/
├── common/          # 공유 모델
│   └── models.py    # IncidentEvent, ResolutionReport, Severity, IncidentStatus
│
├── gateway/         # Event Gateway
│   ├── main.py      # FastAPI 앱, 엔드포인트 정의
│   ├── store.py     # 인메모리 인시던트 스토어 (→ RDB 확장)
│   └── parsers/
│       └── sentry.py  # Sentry 페이로드 → IncidentEvent (→ datadog.py 추가)
│
├── orchestrator/    # Multi-Agent Pipeline
│   ├── agents.py    # Triage / Analyst / Fixer 에이전트 정의
│   ├── runner.py    # Crew 조립 + 실행 (mock/real 분기)
│   └── tools/
│       ├── sentry.py  # Sentry Issue Lookup (현재 Mock)
│       └── github.py  # GitHub Source Lookup (현재 Mock)
│
└── chatops/         # Notifier Interface
    ├── base.py      # Notifier ABC
    └── console.py   # ConsoleNotifier (→ slack.py 추가)
```

## 데이터 흐름

1. `POST /webhook/sentry` → `sentry_parser.parse()` → `IncidentEvent`
2. `IncidentEvent` → `run_pipeline()` → 에이전트 순차 실행
3. 각 에이전트 결과 → `ResolutionReport` 조립
4. `notifier.on_resolution_ready()` → 출력
5. `status: awaiting_approval` 대기
6. `POST /incidents/{id}/approve` or `/reject` → 최종 처리

## 확장 포인트

| 항목 | 위치 | 방법 |
|------|------|------|
| Datadog 웹훅 | `gateway/parsers/datadog.py` | `parse()` 동일 인터페이스 구현 |
| Slack 알림 | `chatops/slack.py` | `Notifier` ABC 구현 |
| RDB 저장 | `gateway/store.py` | `IncidentStore` 클래스 교체 |
| Jira 연동 | `gateway/main.py` approve 핸들러 | TODO 주석 위치 참고 |
| 실제 LLM | `.env` `MOCK_PIPELINE=false` | Anthropic API 크레딧 필요 |
| 실제 Sentry API | `orchestrator/tools/sentry.py` | TODO 주석 위치 교체 |
| 실제 GitHub API | `orchestrator/tools/github.py` | TODO 주석 위치 교체 |
