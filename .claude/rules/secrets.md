# Secrets / Redaction 컨벤션

이 프로젝트는 LLM 출력을 실제 파일로 commit 하고 외부 API (Slack/Sentry/GitHub) 와 통신한다. secret 이 코드/로그/PR 어디에도 새지 않게 하는 규칙. 한 군데서만 어겨도 영구히 incident 됨 (git history, Slack 메시지, GitHub PR — 모두 사후 회수 비싸다).

## 1. 보관 위치

| 종류 | 위치 | 형태 |
|---|---|---|
| API key / token / shared secret | `.env` | `KEY=value` |
| 비대칭키 (RSA pem 등) | `.secrets/` | 별도 파일 (`*.pem`) |
| 테스트용 더미 secret | `monkeypatch.setenv(...)` | 코드 없음 |

`.env` 와 `.secrets/` 둘 다 `.gitignore` 에 등록되어 있다 — **commit 전 `git status` 로 확인 필수**. 새 secret 추가 시 `.env.example` 에는 키 이름과 설명만 (값은 빈칸).

## 2. 코드 내 secret 접근

**`os.getenv` 한 곳만**. hardcoded literal 금지. 미설정 시 동작은 두 패턴 중 하나:

```python
# 패턴 A — secret 없으면 dry-run / 검증 스킵 (dev 편의)
secret = os.getenv("SENTRY_CLIENT_SECRET")
if not secret:
    return True   # dev 한정 스킵, 운영에선 반드시 채워야 함

# 패턴 B — secret 없으면 에러 (운영 전제)
api_key = os.getenv("ANTHROPIC_API_KEY")
if not api_key:
    raise RuntimeError("ANTHROPIC_API_KEY 미설정")
```

운영 전제 모듈은 B, dev 시연 편의 모듈은 A. 둘이 섞이면 안 됨 — `services/security.py` (webhook 서명 검증) 는 A, `orchestrator/llm.py` 는 B.

## 3. LLM 출력 redaction ★ Phase 4 핵심

LLM 이 패치 코드에 secret 형태 문자열을 박는 경우가 있다 (예: 예시 코드에 `OPENAI_KEY="sk-..."` 박음). 이 출력이 그대로 PR 로 commit 되면 git history 에 영구 박힘. **GitHub commit 직전에 redaction 한 번**:

| 패턴 | 대상 |
|---|---|
| `sk-[a-zA-Z0-9_-]{20,}` | OpenAI / Anthropic API key |
| `ghp_[a-zA-Z0-9]{36}` | GitHub PAT |
| `xox[baprs]-[a-zA-Z0-9-]+` | Slack token |
| `AKIA[0-9A-Z]{16}` | AWS access key |
| `-----BEGIN [A-Z ]+PRIVATE KEY-----` | PEM private key |
| `[a-zA-Z0-9]{32,}@.*\.iam\.gserviceaccount\.com` | GCP service account |

검출 시 `[REDACTED:<type>]` 로 치환. redaction 함수는 `common/security.py` (없으면 신설) 한 곳, 모든 LLM-출력 경로 (Slack thread, PR commit, incidents/<id>.md, output/incidents/) 가 통과해야 함.

## 4. 로그 / `print` 안전 매트릭스

| 항목 | 로그 OK? |
|---|---|
| incident_id, PR URL, branch name | ✅ |
| ngrok 도메인 (단순 URL) | ⚠️ 코드에는 OK / **외부 공유 자료에는 마스킹** (지난 주 보고서 사고) |
| Slack channel id (`C0...`) | ✅ |
| Bot Token (`xoxb-...`) | ❌ 절대 금지 |
| Signing Secret | ❌ |
| GitHub installation token | ❌ (1시간 TTL 이라도) |
| GitHub App private key 경로 | ✅ 경로만, 내용 금지 |
| LLM response raw (token 포함 가능) | ⚠️ redaction 후 OK |

`print(f"... {token}")` / f-string 에 token 변수 박는 패턴 금지. 디버그 출력은 길이만 (`print(f"token len={len(token)}")`).

## 5. 테스트 env 누수 차단

테스트 실행 시 개발자 `.env` 의 실 secret 이 의도치 않게 로드되면 — 테스트가 실 Slack/GitHub 호출. 차단은 root `conftest.py` 의 autouse fixture 가 담당:

```python
# conftest.py — 이미 구현됨, 패턴 참고용
@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    import gateway.main   # load_dotenv() 트리거
    for var in ("SENTRY_CLIENT_SECRET", "SLACK_BOT_TOKEN", "GITHUB_APP_ID", ...):
        monkeypatch.delenv(var, raising=False)
```

**새 secret env var 추가 시 conftest 의 `for var in (...)` 튜플에도 반드시 추가** — 누락하면 그 secret 만 누수.

테스트 코드 내부에선 항상 dummy 값:

```python
monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-test-12345")
```

`xoxb-test-...` 같은 dummy 형식 권장 — 실 토큰과 패턴은 같지만 inactive 임을 명시.

## 6. 외부 자료 (보고서 / 스크린샷 / Slack 공유)

캡처 / docx / md / pdf 등 외부 공유 자료에 다음이 보이면 마스킹:

- ngrok 도메인 전체 (인증 없는 endpoint 접근 가능 — 지난 주 보고서 사고)
- Sentry Webhook URL (client secret 일부 노출 가능)
- LLM key (`AIza...`, `sk-...`)
- bot/installation token

마스킹 표기는 `xxxxxx` 또는 검정 블록. 절대 partial 노출 금지 (앞 4글자만 가려도 entropy 적은 부분은 brute-force 가능).

## 7. 새 secret 추가 시 체크리스트

- [ ] `.env.example` 에 키 이름 + 설명 추가
- [ ] `os.getenv` 로 로드, A/B 패턴 중 하나 명시
- [ ] `conftest.py` 의 delenv 튜플에 추가
- [ ] LLM 이 출력할 가능성 있다면 § 3 redaction 패턴에 추가
- [ ] 로그/`print` 에 노출 안 되는지 확인
- [ ] commit 전 `git status` / `git diff` 에 실값 안 보이는지 grep
