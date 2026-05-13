# Warroom CrewAI Agent 연결 플랜 정리

> 검토 시점: 2026-05-06 KST  
> 대상 프로젝트: **Warroom** — AI Agent 기반 서비스 장애 탐지 및 대응 자동화 시스템  
> 목적: CrewAI 기반 Agent 파이프라인을 실제 LLM/API에 연결할 때 가능한 무료/유료 플랜 비교

---

## 0. 핵심 결론

Warroom의 AI Agent 연결 비용은 크게 두 가지로 나눠 봐야 한다.

1. **CrewAI 실행/관리 비용**
   - 로컬/서버에서 직접 CrewAI 프레임워크를 실행하면 별도 CrewAI 플랫폼 비용 없이 시작 가능
   - CrewAI 관리형/플랫폼 기능을 쓰면 Free/Enterprise 등 별도 플랜 검토 필요

2. **LLM API 비용**
   - 실제 Agent가 Claude, Gemini, OpenAI 등 모델을 호출할 때 발생하는 토큰 기반 비용
   - Warroom 구조에서는 보통 LLM API 비용이 핵심 비용이 됨

현재 Warroom README 기준으로 가장 현실적인 추천안은 다음과 같다.

```env
MOCK_PIPELINE=false
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...

WARROOM_TRIAGE_MODEL=claude-haiku-4-5-20251001
WARROOM_ANALYST_MODEL=claude-sonnet-4-6
WARROOM_FIXER_MODEL=claude-sonnet-4-6
WARROOM_ESCALATION_MODEL=claude-opus-4-7

WARROOM_REQUIRE_APPROVAL_BEFORE_PATCH=true
WARROOM_REDACT_SECRETS=true
WARROOM_MONTHLY_BUDGET_USD=50
```

추천 방향은 **Triage는 저비용 모델, Analyst/Fixer는 고성능 모델**로 분리하는 것이다.

---

## 1. 현재 Warroom 구조 기준 Agent 역할

```text
[Sentry Webhook]
      │
      ▼
Gateway(FastAPI)
      │ BackgroundTask
      ▼
Orchestrator(CrewAI)
      ├─ Triage Agent
      ├─ Analyst Agent
      └─ Fixer Agent
      ▼
ChatOps(Console/Slack)
      ▼
awaiting_approval
      ▼
approve / reject
```

각 Agent별 LLM 요구 수준은 다르다.

| Agent | 역할 | 필요한 모델 특성 | 권장 등급 |
|---|---|---|---|
| Triage Agent | 심각도 분류, 영향도 추정, MTTD 추정 | 빠른 응답, 낮은 비용 | 저비용 모델 |
| Analyst Agent | 스택트레이스/로그/이벤트 분석, 5 Whys, Fishbone | 긴 문맥, 추론력, 안정성 | 중상급 모델 |
| Fixer Agent | 패치 코드 제안, 테스트/재발 방지안 작성 | 코딩 능력, 정확성, 보수성 | 고성능 모델 |
| Escalation | 복잡한 장애, 대규모 코드베이스, 재현 어려운 이슈 | 최고 수준 추론/코딩 | 프론티어 모델 |

---

## 2. 플랜 요약표

| 플랜 | 비용 성격 | 추천 단계 | 모델/방식 | 장점 | 한계 |
|---|---|---|---|---|---|
| Plan 0. Mock Only | 무료 | 로컬 개발, API/상태 흐름 검증 | `MOCK_PIPELINE=true` | 비용 0, 빠른 개발 | 실제 AI 품질 검증 불가 |
| Plan 1. Local OSS LLM | 무료 또는 인프라 비용 | 내부 실험, 보안 검증 | Ollama 등 로컬 모델 | 외부 API 호출 없음 | 품질/속도/하드웨어 제약 |
| Plan 2. Gemini Free/Low Cost | 무료~저비용 | 데모, PoC, 초기 MVP | Gemini Flash/Flash-Lite | 비용 낮음, Free Tier 가능 | 운영 장애/코드 데이터 사용 시 정책 확인 필요 |
| Plan 3. Claude Balanced MVP | 사용량 과금 | 실서비스 MVP 추천 | Haiku + Sonnet | 품질/비용 균형 좋음 | 토큰 사용량 관리 필요 |
| Plan 4. Claude Production | 사용량 과금 + 예산 제한 | 실제 운영 | Sonnet 중심 + Opus 예외 사용 | 장애 분석/패치 품질 우선 | 비용 상승 가능 |
| Plan 5. Enterprise | Custom | 보안/감사/조직 운영 | CrewAI Enterprise + Anthropic/Bedrock/Vertex | SSO, 감사, 지원, private infra 가능 | 도입 비용/계약 필요 |

---

## 3. Plan 0 — Mock Only

### 개요

현재 README의 `MOCK_PIPELINE=true`를 유지하는 방식이다. 실제 LLM 호출 없이 Gateway, IncidentStore, approve/reject, ChatOps 흐름을 검증한다.

### 환경변수 예시

```env
MOCK_PIPELINE=true
```

### 적합한 상황

- FastAPI Gateway 개발
- Sentry Webhook payload 파싱 개발
- Incident 상태 전이 검증
- approve/reject HITL 플로우 검증
- ChatOps 메시지 포맷 검증

### 장점

- 비용 0
- API key 불필요
- 테스트 재현성 높음
- CI에서 안정적으로 돌리기 좋음

### 한계

- 실제 root cause 분석 품질 검증 불가
- 실제 패치 제안 품질 검증 불가
- Agent prompt 설계 검증에는 한계 있음

### 판단

**필수 개발 단계**로 유지하는 것이 좋다. 운영 모드와 별개로 테스트/CI에서는 계속 사용하는 것을 권장한다.

---

## 4. Plan 1 — Local OSS LLM

### 개요

Ollama 같은 로컬 LLM 런타임을 통해 CrewAI Agent를 외부 API 없이 실행하는 방식이다.

### 환경변수 예시

```env
MOCK_PIPELINE=false
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://localhost:11434

WARROOM_TRIAGE_MODEL=ollama/llama3.2
WARROOM_ANALYST_MODEL=ollama/qwen2.5-coder
WARROOM_FIXER_MODEL=ollama/qwen2.5-coder
```

### 적합한 상황

- 외부 API 호출을 피해야 하는 내부 실험
- 민감 로그/소스코드 외부 전송 전 보안 검증
- 비용 없는 개발자 데모
- Agent orchestration 동작 검증

### 장점

- API 사용료 없음
- 데이터가 로컬/사내 인프라 밖으로 나가지 않음
- 개발자가 빠르게 반복 실험 가능

### 한계

- 성능 좋은 모델을 돌리려면 GPU/메모리 필요
- Claude Sonnet급 장애 분석/패치 품질을 기대하기 어려움
- 긴 컨텍스트 처리와 복잡한 코드 수정에서 품질 저하 가능

### 판단

**개발/보안 검증용으로는 유용하지만, Warroom 운영용 주 모델로는 비추천**한다.

---

## 5. Plan 2 — Gemini Free/Low Cost

### 개요

Google Gemini API의 Free Tier 또는 저비용 모델을 사용해 PoC/MVP 비용을 낮추는 방식이다.

### 후보 모델

| 용도 | 후보 모델 | 성격 |
|---|---|---|
| Triage | Gemini Flash-Lite | 매우 저렴, 빠름 |
| Analyst | Gemini Flash | 비용 대비 성능 좋음 |
| Fixer | Gemini Flash 또는 Sonnet 병행 | 비용 절감 가능하지만 코드 패치 품질 검증 필요 |

### 환경변수 예시

```env
MOCK_PIPELINE=false
LLM_PROVIDER=gemini
GEMINI_API_KEY=...

WARROOM_TRIAGE_MODEL=gemini-2.5-flash-lite
WARROOM_ANALYST_MODEL=gemini-2.5-flash
WARROOM_FIXER_MODEL=gemini-2.5-flash
```

### 적합한 상황

- 사내 데모
- 초기 PoC
- 장애 분석 결과 포맷 검증
- 대량 이벤트 triage 비용 절감

### 장점

- 무료 또는 매우 낮은 비용으로 시작 가능
- 속도와 비용 효율이 좋음
- Triage, 요약, 분류 작업에 적합

### 한계 및 주의

- 운영 장애 로그, 스택트레이스, 소스코드 등 민감 데이터가 포함될 수 있으므로 Free Tier/데이터 사용 정책 확인 필요
- Fixer Agent의 패치 제안 품질은 반드시 별도 평가 필요
- 장애 대응 자동화에서는 잘못된 패치 제안의 리스크가 큼

### 판단

**PoC/데모에는 좋지만, 운영 Fixer Agent는 Claude Sonnet 이상을 권장**한다.

---

## 6. Plan 3 — Claude Balanced MVP Recommended

### 개요

Warroom 실서비스 MVP에 가장 추천하는 구성이다.

- Triage Agent: Claude Haiku 4.5
- Analyst Agent: Claude Sonnet 4.6
- Fixer Agent: Claude Sonnet 4.6
- 복잡한 이슈만 Escalation 모델로 Claude Opus 4.7 선택 사용

### 환경변수 예시

```env
MOCK_PIPELINE=false
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...

WARROOM_TRIAGE_MODEL=claude-haiku-4-5-20251001
WARROOM_ANALYST_MODEL=claude-sonnet-4-6
WARROOM_FIXER_MODEL=claude-sonnet-4-6
WARROOM_ESCALATION_MODEL=claude-opus-4-7

WARROOM_REQUIRE_APPROVAL_BEFORE_PATCH=true
WARROOM_REDACT_SECRETS=true
WARROOM_MONTHLY_BUDGET_USD=50
```

### Agent별 권장 모델

| Agent | 권장 모델 | 이유 |
|---|---|---|
| Triage Agent | `claude-haiku-4-5-20251001` | 빠르고 저렴함. 심각도 분류/우선순위 판단에 적합 |
| Analyst Agent | `claude-sonnet-4-6` | 긴 문맥, 로그/스택 분석, RCA에 적합 |
| Fixer Agent | `claude-sonnet-4-6` | 코드 패치 제안 품질과 안정성 필요 |
| Escalation | `claude-opus-4-7` | 복잡한 agentic coding / 고난도 원인 분석에만 제한 사용 |

### 장점

- README의 Anthropic API 전제와 가장 잘 맞음
- 비용과 품질 균형이 좋음
- 장애 분석과 패치 제안 품질 확보 가능
- Agent별 모델 분리로 비용 최적화 가능

### 한계

- 실제 비용은 Sentry payload, 로그, repo context, Agent 재시도 횟수에 따라 증가
- 소스코드/로그 전송 전 secret redaction 필수
- 예산 제한과 rate limit 처리가 필요

### 판단

**Warroom MVP의 기본 추천 플랜**이다.

---

## 7. Plan 4 — Claude Production

### 개요

실제 운영 환경에서 안정성과 품질을 우선하는 구성이다.

기본은 Sonnet 4.6을 중심으로 하고, 특정 조건에서만 Opus 4.7로 escalation한다.

### 환경변수 예시

```env
MOCK_PIPELINE=false
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...

WARROOM_TRIAGE_MODEL=claude-haiku-4-5-20251001
WARROOM_ANALYST_MODEL=claude-sonnet-4-6
WARROOM_FIXER_MODEL=claude-sonnet-4-6
WARROOM_ESCALATION_MODEL=claude-opus-4-7

WARROOM_ENABLE_PROMPT_CACHE=true
WARROOM_MAX_INPUT_TOKENS_PER_INCIDENT=120000
WARROOM_MAX_AGENT_RETRIES=1
WARROOM_REQUIRE_APPROVAL_BEFORE_PATCH=true
WARROOM_REDACT_SECRETS=true
WARROOM_STORE_AUDIT_LOG=true
WARROOM_MONTHLY_BUDGET_USD=300
```

### Escalation 조건 예시

아래 조건 중 하나 이상이면 Opus 사용을 고려한다.

- 동일 이슈가 N회 이상 재발
- Fixer의 confidence가 낮음
- 장애 영향도가 `critical` 이상
- 여러 서비스/패키지에 걸친 변경이 필요
- 기존 Sonnet 분석 결과가 불충분하거나 모순됨
- incident가 결제/인증/데이터 손실과 관련됨

### 운영 시 필수 가드레일

| 항목 | 권장 설정 |
|---|---|
| Secret redaction | API key, token, password, connection string 제거 후 LLM 전송 |
| Budget limit | 월/일/incident 단위 예산 제한 |
| Token cap | incident당 최대 입력 토큰 제한 |
| Retry cap | Agent 재시도 횟수 제한 |
| HITL | 패치 적용 전 개발자 승인 필수 |
| Audit log | LLM 입력/출력 요약, 결정 사유, 승인자 기록 |
| Diff only | Fixer는 전체 파일보다 최소 diff 중심 출력 |
| Test command | 패치 제안 시 테스트 명령 포함 |

### 판단

**운영 전환 단계에서 가장 안전한 구성**이다. 단, 비용 통제를 위해 Opus는 예외 상황에만 사용한다.

---

## 8. Plan 5 — Enterprise

### 개요

조직 보안, 감사, 접근 제어, 사내 인프라 요건이 커질 경우의 구성이다.

### 후보 구성

- CrewAI Enterprise 또는 private infrastructure
- Anthropic Claude API 직접 사용
- 또는 AWS Bedrock / Google Vertex AI 경유 사용
- SSO, RBAC, 감사 로그, 보안 정책, VPC/Private networking 검토

### 적합한 상황

- 운영 장애 로그에 민감정보가 많음
- 금융/헬스케어/공공 등 규제가 있는 환경
- 조직 단위 사용자 권한 관리 필요
- Agent 실행 이력 및 승인 이력 감사 필요
- Slack/GitHub/Sentry 연동 권한을 중앙 관리해야 함

### 장점

- 보안/거버넌스 요구사항 대응 가능
- 조직 단위 운영과 감사에 적합
- 대규모 운영 시 지원/계약 기반 대응 가능

### 한계

- 도입 비용이 커짐
- 계약/보안 검토 기간 필요
- MVP 단계에서는 과할 수 있음

### 판단

**초기 MVP 이후, 실제 운영 조직에서 보안/감사 요구가 명확해질 때 검토**하는 것이 좋다.

---

## 9. Claude API 비용 예시

아래는 단순 계산 예시다. 실제 비용은 각 Agent 호출 횟수, 입력 로그 크기, repo context 크기, 재시도 횟수에 따라 달라진다.

기준 가격:

| 모델 | Input | Output | 용도 |
|---|---:|---:|---|
| Claude Haiku 4.5 | $1 / MTok | $5 / MTok | Triage, 요약, 분류 |
| Claude Sonnet 4.6 | $3 / MTok | $15 / MTok | RCA, 분석, 패치 제안 |
| Claude Opus 4.7 | $5 / MTok | $25 / MTok | 복잡한 agentic coding, escalation |

MTok = 1,000,000 tokens

### 사건 1건당 대략 비용

| 시나리오 | 입력 토큰 | 출력 토큰 | Haiku 4.5 | Sonnet 4.6 | Opus 4.7 |
|---|---:|---:|---:|---:|---:|
| 가벼운 장애 | 10k | 2k | 약 $0.02 | 약 $0.06 | 약 $0.10 |
| 보통 장애 | 30k | 8k | 약 $0.07 | 약 $0.21 | 약 $0.35 |
| 무거운 장애 | 100k | 25k | 약 $0.23 | 약 $0.68 | 약 $1.13 |

### 비용 계산식

```text
비용 = input_tokens / 1,000,000 * input_price
     + output_tokens / 1,000,000 * output_price
```

예: Sonnet 4.6으로 보통 장애 1건 처리

```text
30,000 / 1,000,000 * 3
+ 8,000 / 1,000,000 * 15
= 0.09 + 0.12
= $0.21
```

주의: CrewAI에서 Triage, Analyst, Fixer가 각각 LLM을 호출하면 incident 1건 비용은 세 Agent 호출 비용의 합으로 계산해야 한다.

---

## 10. 추천 로드맵

### Phase 1 — 개발 안정화

```env
MOCK_PIPELINE=true
```

목표:

- Gateway webhook 수신 안정화
- Incident 상태 모델 정리
- approve/reject API 완성
- Console ChatOps 메시지 포맷 확정

### Phase 2 — AI 연결 PoC

```env
LLM_PROVIDER=anthropic
WARROOM_TRIAGE_MODEL=claude-haiku-4-5-20251001
WARROOM_ANALYST_MODEL=claude-sonnet-4-6
WARROOM_FIXER_MODEL=claude-sonnet-4-6
WARROOM_MONTHLY_BUDGET_USD=50
```

목표:

- 실제 Sentry payload로 RCA 품질 평가
- Fixer patch diff 품질 평가
- prompt template 정리
- token usage logging 추가

### Phase 3 — 운영 MVP

추가할 것:

- secret redaction
- token cap
- budget cap
- retry cap
- incident audit log
- Slack approval flow
- GitHub PR 생성은 자동 적용이 아니라 제안/승인 기반으로 제한

### Phase 4 — 운영 고도화

추가할 것:

- 실제 Sentry API 연동
- GitHub API 연동
- RDB 기반 IncidentStore
- Slack notifier
- prompt caching
- 모델 fallback
- escalation policy
- evaluation dataset 구축

---

## 11. 구현 메모

CrewAI에서는 Agent별로 LLM을 분리해서 설정하는 것이 좋다.

```python
# packages/orchestrator/llm.py

import os
from crewai import LLM


def make_llm(model_env: str, default_model: str) -> LLM:
    provider = os.getenv("LLM_PROVIDER", "anthropic")
    model = os.getenv(model_env, default_model)

    if provider == "anthropic":
        return LLM(
            model=model,
            api_key=os.environ["ANTHROPIC_API_KEY"],
            temperature=0.2,
        )

    if provider == "gemini":
        return LLM(
            model=model,
            api_key=os.environ["GEMINI_API_KEY"],
            temperature=0.2,
        )

    if provider == "ollama":
        return LLM(
            model=model,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434"),
            temperature=0.2,
        )

    raise ValueError(f"Unsupported LLM_PROVIDER: {provider}")


triage_llm = make_llm(
    "WARROOM_TRIAGE_MODEL",
    "claude-haiku-4-5-20251001",
)

analyst_llm = make_llm(
    "WARROOM_ANALYST_MODEL",
    "claude-sonnet-4-6",
)

fixer_llm = make_llm(
    "WARROOM_FIXER_MODEL",
    "claude-sonnet-4-6",
)

escalation_llm = make_llm(
    "WARROOM_ESCALATION_MODEL",
    "claude-opus-4-7",
)
```

---

## 12. 최종 추천안

Warroom은 단순 챗봇이 아니라 **서비스 장애 분석 + 패치 제안** 시스템이다. 따라서 운영 단계에서는 무료 모델만으로 끝까지 가기보다, 비용이 조금 들더라도 분석/패치 품질을 확보하는 것이 안전하다.

최종 추천은 다음과 같다.

```env
MOCK_PIPELINE=false
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=...

WARROOM_TRIAGE_MODEL=claude-haiku-4-5-20251001
WARROOM_ANALYST_MODEL=claude-sonnet-4-6
WARROOM_FIXER_MODEL=claude-sonnet-4-6
WARROOM_ESCALATION_MODEL=claude-opus-4-7

WARROOM_REQUIRE_APPROVAL_BEFORE_PATCH=true
WARROOM_REDACT_SECRETS=true
WARROOM_MAX_AGENT_RETRIES=1
WARROOM_MAX_INPUT_TOKENS_PER_INCIDENT=120000
WARROOM_MONTHLY_BUDGET_USD=50
```

운영 정책:

1. **초기 개발/CI**는 `MOCK_PIPELINE=true`
2. **PoC/MVP**는 Haiku + Sonnet 조합
3. **실제 운영**은 Sonnet 중심, Opus는 critical/escalation에만 사용
4. **패치 적용은 항상 Human-in-the-Loop 승인 후 진행**
5. **토큰 사용량, 비용, 승인 이력은 incident별로 저장**

---

## 13. 참고 자료

- Anthropic Claude API pricing: https://platform.claude.com/docs/en/about-claude/pricing
- Anthropic Claude models overview: https://platform.claude.com/docs/en/about-claude/models/overview
- CrewAI pricing: https://crewai.com/pricing
- CrewAI LLM connections: https://docs.crewai.com/en/learn/llm-connections
- Google Gemini API pricing: https://ai.google.dev/gemini-api/docs/pricing

