# 환경 셋업 가이드

외부 서비스 4종 (Slack / Sentry / ngrok / GitHub App) 의 가입·설정·`.env` 작성을 한 번에 끝내는 체크리스트.

> 진행 순서는 **0.1 → 0.2 → 0.3 → 0.4** 권장. 각 단계 끝의 **검증** 으로 다음 넘어가기 전 확인.

| 단계 | 목적 | 코드 의존 |
|---|---|---|
| 0.1 Slack App | 알림 송신 + 버튼 + 서명 검증 | Phase 2/3 |
| 0.2 Sentry SaaS | 실 webhook 송신 + 서명 검증 | Phase 1.5 (이미 구현) |
| 0.2b ngrok | SaaS 가 로컬 gateway 도달 가능하게 터널링 | — |
| 0.3 GitHub App | 승인된 패치를 PR 로 자동 생성 | Phase 4 (현재 dry-run) |
| 0.4 LLM Provider | Triage/Analyst/Fixer 실 호출 | 전체 |

---

## 0.1 Slack App

### 생성

1. https://api.slack.com/apps → **Create New App** → **From scratch**
2. **App Name**: `warroom` (자유) / **Workspace**: 개인 워크스페이스 또는 테스트용
3. 좌측 메뉴 **OAuth & Permissions** → **Scopes / Bot Token Scopes** 추가:
   - `chat:write` — 메시지 송신
   - `chat:write.public` — 봇이 가입하지 않은 채널에도 송신
   - `users:read` — (선택) 사용자 이름 표시용
4. 상단 **Install to Workspace** → 권한 승인
5. **Bot User OAuth Token** (`xoxb-...`) 복사
6. 좌측 **Basic Information** → **App-Level Tokens / Signing Secret** 복사 (이건 Phase 3 Interactivity 검증용, 미리 받아둠)

### `.env` 작성

```bash
SLACK_BOT_TOKEN=xoxb-1234...
SLACK_SIGNING_SECRET=abcd1234...
# Phase 2 이전에는 기존 incoming webhook 도 병행 가능
# SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
WARROOM_NOTIFIER=both    # console + slack
```

### 검증

```bash
# Bot Token 으로 자기 자신에게 DM (channel id = bot user id 사용 시 self-test)
curl -X POST https://slack.com/api/auth.test \
  -H "Authorization: Bearer $SLACK_BOT_TOKEN"
# {"ok":true,"user":"warroom","team":"..."} 이면 성공
```

> Phase 2 에서 `SlackNotifier` 를 `chat.postMessage` 기반으로 교체할 때 이 토큰 사용. 그때까지는 incoming webhook 으로도 동작 가능.

---

## 0.2 Sentry SaaS Free Tier

월 5,000 에러까지 무료. PoC 데모 한 번에 못 채움.

### 가입 & 프로젝트

1. https://sentry.io → **Sign Up** (GitHub 로그인 추천)
2. Onboarding 에서 **Platform: Python** 선택 (실제로는 webhook 만 받지만 Sentry UI 에서 테스트 이슈 생성용)
3. **Project name**: `warroom-demo`

### Custom Integration (webhook 발송 주체)

1. 좌측 **Settings** → **Custom Integrations** (Developer Settings) → **New Internal Integration**
2. **Name**: `warroom-gateway`
3. **Webhook URL**: `https://<ngrok-subdomain>.ngrok-free.app/webhook/sentry` _(ngrok URL 은 0.2b 에서 발급)_
4. **Alert Rule Action**: enable (Issue alert 시 이 integration 으로 전송)
5. **Permissions**:
   - Issue & Event: **Read**
   - 나머지: No Access
6. **Webhooks**: `issue` checkbox enable
7. **Save** → 생성된 페이지의 **Client Secret** 복사

### `.env` 작성

```bash
SENTRY_CLIENT_SECRET=<client secret>
```

> 이 비밀은 `packages/gateway/gateway/security.py` 의 `verify_sentry_signature` 가 HMAC-SHA256 검증에 사용. **미설정 시 dev 한정으로 검증 스킵** (운영에선 반드시 채워야 함).

### Alert Rule 등록 (실제로 webhook 트리거)

1. **Alerts** → **Create Alert** → **Issues** 선택
2. **WHEN**: A new issue is created (또는 임의 조건)
3. **THEN**: Send a notification via **Integration** → `warroom-gateway` 선택
4. Save

### 검증 (ngrok 까지 띄운 후)

Sentry UI 에서 의도적 에러 발생:
1. 좌측 **Issues** → **Send Test Event** (또는 SDK 로 `raise Exception("test")` 한 번)
2. gateway 로그에 `[WARROOM] 인시던트 수신 ...` 라인 확인

---

## 0.2b ngrok

Sentry SaaS 가 인터넷 → 로컬 8000 포트 도달하게 터널링.

### 설치 & 인증

```bash
brew install ngrok
# 또는 https://ngrok.com/download

# 무료 가입 후 dashboard.ngrok.com 에서 authtoken 발급
ngrok config add-authtoken <token>
```

### 실행

```bash
ngrok http 8000
```

출력에서 `Forwarding https://abc-12-34-56-78.ngrok-free.app -> http://localhost:8000` 형식 URL 복사 → 0.2 의 **Webhook URL** 에 사용.

### 주의

- 무료 plan 은 **세션마다 URL 변경** (재시작 시 Sentry 측 webhook URL 재등록 필요)
- 발표 직전이면 ngrok 먼저 띄우고 URL 고정 → Sentry 등록 → 시연
- 더 안정적인 옵션: ngrok 유료 (`ngrok http --domain=fixed.ngrok.app 8000`) 또는 Cloudflare Tunnel

---

## 0.3 GitHub App

승인된 인시던트는 `incidents/<id>.md` 파일을 만든 새 브랜치를 push 하고 PR 을 open. App credentials 가 없으면 dry-run 으로 `output/github_payloads.jsonl` 에 페이로드만 기록.

### 생성 & 권한

1. https://github.com/settings/apps/new
2. **GitHub App name**: `warroom-<your>`
3. **Homepage URL**: 임의 (`https://github.com/<you>/warroom`)
4. **Webhook**: Active 체크 해제 (warroom 에서는 webhook 수신 안 함, 호출만)
5. **Permissions** (Repository):
   - **Contents**: Read & write
   - **Pull requests**: Write
   - **Metadata**: Read (default)
6. **Where can this app be installed**: Only on this account
7. **Create GitHub App** → 생성 페이지에서 **App ID** 메모

### Private key 다운로드

생성 페이지 하단 **Private keys** → **Generate a private key** → `.pem` 다운로드:

```bash
mkdir -p .secrets
mv ~/Downloads/warroom-*.pem .secrets/warroom-app.private-key.pem
chmod 600 .secrets/warroom-app.private-key.pem
```

> `.secrets/` 는 `.gitignore` 에 등록되어 있어 commit 안 됨.

### 데모 레포에 install

1. App 페이지 좌측 **Install App** → 본인 계정 선택
2. **Only select repositories** → `warroom-demo` (없으면 https://github.com/new 에서 빈 레포 하나 만들고 install)
3. install 후 URL 의 `installation_id` 메모 (`https://github.com/settings/installations/<ID>`)

### `.env` 작성

```bash
GITHUB_REPO=<owner>/warroom-demo
GITHUB_APP_ID=<App ID>
GITHUB_APP_PRIVATE_KEY_PATH=./.secrets/warroom-app.private-key.pem
GITHUB_INSTALLATION_ID=<Installation ID>
```

### 검증

```bash
MOCK_PIPELINE=true uv run demo.py
# y 입력 → PR URL 출력 (dry-run 아닌 실 PR)
# warroom-demo 레포에 PR 생성 확인
```

---

## 0.4 LLM Provider

| Provider | 가입 | 비용 |
|---|---|---|
| **Gemini** (기본) | https://aistudio.google.com/apikey | 무료 (Free Tier) |
| **Anthropic** (운영 권장) | https://console.anthropic.com/settings/keys | 유료, $5+ 충전 권장 |
| **Ollama** (로컬) | `brew install ollama` + `ollama pull <model>` | 무료, 로컬 자원 |

### `.env` 작성

```bash
LLM_PROVIDER=gemini   # gemini | anthropic | ollama
GEMINI_API_KEY=AIza...
# ANTHROPIC_API_KEY=sk-ant-...
MOCK_PIPELINE=false
```

### 검증

```bash
uv run demo.py
# 첫 줄에 "[WARROOM] LLM Provider: gemini" 출력 + 파이프라인 동작 확인
```

---

## 최종 `.env` 체크리스트

```bash
# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...

# Sentry
SENTRY_CLIENT_SECRET=...

# GitHub App
GITHUB_REPO=<owner>/<repo>
GITHUB_APP_ID=...
GITHUB_APP_PRIVATE_KEY_PATH=./.secrets/warroom-app.private-key.pem
GITHUB_INSTALLATION_ID=...

# LLM
LLM_PROVIDER=gemini
GEMINI_API_KEY=...

# DB (MySQL 사용 시)
DATABASE_URL=mysql+asyncmy://warroom:warroom@localhost:3306/warroom?init_command=SET%20time_zone%3D%27%2B00:00%27
```

### 동작 확인

```bash
# 1. MySQL 띄우기 (DB MySQL 사용 시)
docker compose up -d
uv run alembic upgrade head

# 2. 서버 + ngrok 동시 띄우기 (별 터미널 2개)
uv run serve.py
ngrok http 8000

# 3. Sentry UI 에서 Send Test Event
# 4. gateway 로그 확인: incident received → Triage → Slack 알림
# 5. Slack 에서 ✅ 클릭 (Phase 3 이후) 또는 HTTP /approve
curl -X POST http://localhost:8000/incidents/<id>/approve
# 6. GitHub 에서 PR 생성 확인
```

---

## Trouble shooting

| 증상 | 원인 | 해결 |
|---|---|---|
| `Invalid Sentry signature` 401 | `SENTRY_CLIENT_SECRET` 불일치 | Custom Integration 페이지의 Client Secret 재확인. 환경변수 reload (`source .env` 또는 서버 재시작) |
| ngrok 무료 plan 으로 매번 URL 바뀜 | 무료 한계 | 시연 직전 한번 띄우고 Sentry webhook URL 재등록. 영구 필요시 유료 |
| GitHub App PR 생성 시 `404 Not Found` | repo 에 app install 안 됨 | https://github.com/settings/installations 에서 해당 repo 추가 |
| Slack 메시지 안 옴 | Bot 이 채널에 invite 안 됨 + `chat:write.public` 없음 | scope 추가 후 재설치, 또는 채널에서 `/invite @warroom` |
| Slack 401 `invalid_auth` | Bot Token 오타 / re-install 안 함 | OAuth & Permissions 페이지에서 토큰 재복사 |
