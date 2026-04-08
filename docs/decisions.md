# 프로젝트 기획 및 기술 결정 사항

> 2026-04-09 기준으로 정리된 설계/기술 결정 내용

---

## 프로젝트 개요

**주제:** AI Agent 기반 서비스 장애 탐지 및 대응 자동화 (AIOps War Room)

**목표:** Sentry 등 모니터링 툴의 알람을 시작점으로, 여러 AI 에이전트가 자율 협업하여 에러 로그 분석 → 원인 코드 추적 → 패치 제안까지 수행하는 자동화된 ChatOps 워룸 구축.

**핵심 차별점:**
- 단일 챗봇이 아닌 **Multi-Agent 협업** (역할별 페르소나 분리)
- 정적 알람이 아닌 **Context-Awareness** (Sentry/GitHub API 자율 호출)
- **Human-in-the-Loop** — 최종 패치 적용 전 개발자 승인 필수

---

## 확정 기술 스택

| 레이어 | 기술 | 비고 |
|--------|------|------|
| **Runtime** | Python 3.13 (uv 관리) | uv로 버전 고정 |
| **Event Gateway** | FastAPI + uvicorn | 비동기, BackgroundTasks |
| **AI Orchestration** | CrewAI 0.80+ | Sequential Process |
| **LLM** | Anthropic Claude Sonnet 4.6 | claude-sonnet-4-6 |
| **ChatOps** | Slack SDK (콘솔 stub → 실제 교체) | |
| **데이터 저장** | 프로토타입은 메모리/JSON | 추후 RDB 확장 포인트 |

---

## 아키텍처: 3개 서브모듈

### Sub-module 1: Event Gateway (FastAPI)

- `/webhook/sentry`, `/webhook/datadog` 등 소스별 엔드포인트 수신
- **즉시 `200 OK` 반환** + `BackgroundTasks`로 파이프라인 비동기 트리거
- 소스(Sentry/Datadog)에 따라 파서를 교체하는 구조 → **새 소스 추가 시 파서만 추가**

### Sub-module 2: Multi-Agent Orchestration (CrewAI)

순차(Sequential) 실행:

```
Triage Agent
  └─ 에러 페이로드 분석, 심각도 분류, 초기 브리핑 작성

Analyst Agent
  ├─ Tool: Sentry Issue Lookup  (상세 스택트레이스 조회)
  └─ Tool: GitHub Source Lookup (관련 소스코드 조회)
  └─ 근본 원인(Root Cause) 추론

Fixer Agent
  └─ 패치 코드 스니펫 제안 + 포스트모템 초안 작성
```

> **주의:** Fixer의 출력은 "패치 **제안**"이며, 자동 적용되지 않음. 개발자 승인 필요.

### Sub-module 3: ChatOps Interface

- 각 에이전트 진행 과정을 실시간 출력 (프로토타입: 콘솔, 이후: Slack Thread)
- 최종 결과에 승인(Approve) / 반려(Reject) 액션 제공
  - 승인 → 포스트모템 저장 (+ 추후 Jira 티켓 생성 확장 포인트)
  - 반려 → 재분석 요청 (피드백 포함)

---

## 프로토타입 범위 (Milestone 1)

| 항목 | 프로토타입 | 향후 확장 |
|------|-----------|----------|
| 인시던트 소스 | **Sentry** 우선 구현 | Datadog 파서 추가 |
| 외부 API Tool | **Mock** (더미 응답) | 실제 Sentry/GitHub API 교체 |
| ChatOps | **콘솔 출력** | Slack SDK 교체 |
| 승인/반려 | **CLI 입력** (y/n) | Slack Interactive Button |
| 티켓/이력 | **JSON 파일 저장** | Jira API 연동, RDB 저장 |

---

## 코드 구조 원칙

1. **파서 분리:** `parsers/sentry.py`, `parsers/datadog.py` — 소스 추가 시 파일만 추가
2. **Tool 분리:** `crew/tools/sentry.py`, `crew/tools/github.py` — Mock 함수를 실제 API 호출로 교체
3. **Notifier 인터페이스:** `ConsoleNotifier`, `SlackNotifier` 동일 인터페이스 — 환경변수로 교체
4. **Action Handler:** 승인/반려 핸들러를 인터페이스로 분리 — CLI → Slack 교체

---

## 비기능 요구사항 (변경 없음)

- Webhook 수신 후 **1초 이내 200 OK** 반환 (BackgroundTasks 활용)
- LLM Rate Limit / 외부 API 실패 시 크래시 없이 Fallback 메시지 출력
- API Key 등 민감정보는 `.env` 관리, 코드 하드코딩 금지
- Fixer 출력 코드는 반드시 개발자 검토 후 적용 (자동 배포 없음)
