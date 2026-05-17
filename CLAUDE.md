# CLAUDE.md

AI Agent 기반 서비스 장애 탐지·대응 자동화 시스템. uv workspace (Python 3.13), FastAPI + CrewAI + Anthropic Claude.

상세 설계: `docs/decisions.md` / 아키텍처: `docs/architecture.md` / 구현 플랜: `docs/plan.md`

엔지니어링 컨벤션: @.claude/rules/layering.md / @.claude/rules/db.md / @.claude/rules/testing.md / @.claude/rules/async.md / @.claude/rules/datetime.md / @.claude/rules/lint.md / @.claude/rules/secrets.md

## 실행

```bash
uv run serve.py        # 서버
uv run demo.py         # CLI
```

## 핵심 제약

- Gateway는 1초 이내 응답 (BackgroundTasks 사용, 블로킹 금지)
- API 키는 `.env` 에만, 코드 하드코딩 금지
- 패치는 개발자 승인 후에만 적용

## Commit Convention

`<emoji> <type>(<scope>): <subject>`

| emoji | type | 용도 |
|-------|------|------|
| ✨ | feat | 새 기능 |
| 🐛 | fix | 버그 수정 |
| ♻️ | refactor | 코드 개선 |
| 📝 | docs | 문서 |
| 🧪 | test | 테스트 |
| 🔧 | chore | 설정/패키지 |
| 🏗️ | build | 구조 변경 |

scope: `common` `gateway` `orchestrator` `chatops` `github`

**Co-Authored-By 트레일러**: AI assistant (Claude) 가 작성/공동 작성한 commit 은 message 마지막 줄에 트레일러 추가.

```
Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>
```

목적: attribution 추적 + 추후 `git log --invert-grep="Co-Authored-By: Claude"` 로 사람-only commit 만 필터링 가능. 사람만 작성한 commit 에는 붙이지 말 것.
