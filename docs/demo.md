# 데모 시나리오 (End-to-End)

Sentry 알림 → Warroom 분석 → Slack 클릭 → GitHub PR 생성까지 한 호흡으로 따라가는 walkthrough.

## 전제

- [`docs/setup.md`](./setup.md) 의 Slack / Sentry / GitHub App / ngrok 셋업 완료
- `.env` 에 다음이 모두 채워져 있음:
  - `GEMINI_API_KEY` (또는 `LLM_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`)
  - `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`, `SLACK_CHANNEL=#warroom-alerts`
  - `SENTRY_CLIENT_SECRET` (Sentry Internal Integration 의 Client Secret)
  - `GITHUB_REPO`, `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY_PATH`, `GITHUB_INSTALLATION_ID`
  - 시연용 별도 demo repo (예: `dlwodnjs724/warroom-demo`) 에 결함 코드 (`scripts/demo_raise.py` 가 raise 하는 패턴) 미리 commit
- (선택) `SENTRY_DSN` — Warroom 자체의 self-monitoring (dogfooding) 활성화

## 단계

### 1. 인프라 기동

```bash
# DB (MySQL — dev 권장. 또는 SQLite 자동)
docker compose up -d
uv run alembic upgrade head

# ngrok — Sentry 가 호출할 HTTPS endpoint 노출
ngrok http 8000 --domain=<your-static-domain>.ngrok-free.dev
```

### 2. Sentry Webhook URL 등록

Sentry Internal Integration 설정 → Webhooks → `https://<ngrok-domain>/webhook/sentry`. Slack Interactivity URL 도 같은 도메인의 `/slack/interactions` 로 등록.

### 3. Warroom Gateway 실행

```bash
uv run serve.py
```

기대 로그:

```
[WARROOM] Gateway 시작
[WARROOM] Sentry self-monitoring 활성화 (SENTRY_DSN 감지)   # SENTRY_DSN 설정 시
[WARROOM] SQLite 자동 스키마 셋업 완료 ...                    # SQLite 백엔드인 경우
[WARROOM] GitHub client 초기화 (GITHUB_REPO=owner/demo)
```

health check 빠르게:

```bash
curl http://localhost:8000/healthz   # {"status":"ok"}
```

### 4. 결함 코드 raise → Sentry capture

```bash
uv run scripts/demo_raise.py
```

`charge()` / `capture()` / `void()` / `FeeCalculator` / `CurrencyConverter` 의 결함 패턴 (lazy init 누락 / 캐시 미초기화 / dict 키 누락) 을 의도적으로 raise 하고 `sentry-sdk` 로 capture. Sentry 가 alert rule 에 따라 Warroom webhook 호출.

### 5. Warroom 파이프라인 (자동)

`gateway/api/webhooks.py:/webhook/sentry` 가 즉시 `202 Accepted` 반환 → `BackgroundTasks` 가 `services.pipeline.run_incident_pipeline` 실행:

1. `Triage` — 심각도 / category (code/infra/external/operational) 분류
2. `Analyst` — 5 Whys / Fishbone (현재는 mock data, Phase 6.2 에서 실 Sentry/GitHub API)
3. `Fixer` — unified diff 패치 + 포스트모템 초안

각 단계가 Slack thread 에 실시간 reply (`on_agent_update`). 완료 시 메인 메시지가 ✅/❌ 버튼으로 update.

### 6. Slack 에서 승인 (Human-in-the-Loop)

`#warroom-alerts` 채널에 메시지가 뜸:

```
🚨 [Sentry] NullPointerException in stripe.charge
Severity: HIGH | Category: code
[thread] 🧠 Triage → 🔍 Analyst → 🛠️ Fixer 완료
[✅ 승인 / 패치 PR 생성]   [❌ 반려 / 사유 입력]
```

- **✅ 클릭** → 메인 thread 에 `✅ 승인 — PR 생성 중...` reply + `<@USERID>` mention. 5초 내 `🎉 PR #N 생성 완료: https://github.com/owner/demo/pull/N`
- **❌ 클릭** → modal (`views.open`) 으로 반려 사유 입력 → submit → `❌ 반려` + 사유 영속화 (`incidents.rejection_reason` 컬럼). 사유는 9개 secret 패턴 redaction + 4000자 cap

### 7. GitHub PR 리뷰

자동 생성된 PR 본문:
- **유닛 diff** (코드 변경) — base SHA 에 `git apply` 검증 후 blob/tree/commit/ref 생성
- **`incidents/<id>.md`** — Triage 요약 / 근본 원인 / Fixer 패치 / 포스트모템 (단기/중기/장기 액션 + KPI)
- 추출/검증/적용 실패 시 markdown-only 폴백 (분석 리포트만 단일 commit)

PR merge → CI/CD 가 배포. 반려 시 PR 자동 close + branch 삭제.

## 트러블슈팅

| 증상 | 원인 / 확인 | 해결 |
|---|---|---|
| Sentry → Warroom 401 | `Sentry-Hook-Signature` 헤더가 `SENTRY_CLIENT_SECRET` 과 mismatch | Internal Integration 의 Client Secret 재복사. 미설정 시 dev 폴백 (서명 검증 skip) |
| Slack 버튼 클릭 무반응 | `SLACK_SIGNING_SECRET` mismatch 또는 ngrok 도메인 stale | Slack App > Interactivity URL 갱신, 서명 secret 재확인 |
| PR 생성 시 401 / 403 | GitHub App installation token 만료 / PEM 경로 stale | `.env` `GITHUB_APP_PRIVATE_KEY_PATH` 확인, App 재설치 |
| `git apply --check` 실패 → markdown 폴백 | LLM 의 diff 가 base SHA 와 라인 mismatch | mock 데이터 사용 중이면 정상 (`MOCK_PIPELINE=true`). 실 LLM 모드면 demo repo 의 base 코드 확인 |
| `ANALYZING` 상태에서 stuck | 서버 비정상 종료 | 다음 startup 에 자동 `FAILED` 마킹 (`recover_stale_analyzing`) |

## 시연 영상용 환경 마스킹

녹화 / 캡처 시 다음은 반드시 마스킹 (`secrets.md § 6`):

- ngrok 도메인 전체 (인증 없는 endpoint 가 외부에서 접근 가능)
- Sentry Webhook URL (client secret 일부 노출 가능)
- Slack bot token / GitHub installation token (1시간 TTL 이라도)
- LLM key (`AIza...`, `sk-...`)

## Mock 모드만으로 흐름 검증 (외부 서비스 0)

`.env` 에 `MOCK_PIPELINE=true` 만 두고:

```bash
uv run demo.py   # CLI 진입 — webhook payload 시뮬레이션 + mock pipeline + dry-run notifier/github
```

또는 통합 테스트가 같은 경로를 봉인된 환경에서 검증:

```bash
uv run pytest tests/test_e2e_pipeline.py -v
```
