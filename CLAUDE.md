# CLAUDE.md

AI Agent 기반 서비스 장애 탐지·대응 자동화 시스템. uv workspace (Python 3.13), FastAPI + CrewAI + Anthropic Claude.

상세 설계: `docs/decisions.md` / 아키텍처: `docs/architecture.md`

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

scope: `common` `gateway` `orchestrator` `chatops`
