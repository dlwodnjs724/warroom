# Lint / Format 컨벤션

## 1. 도구

| 책임 | 도구 | 명령 |
|---|---|---|
| Format (전체) | ruff format | `uv run ruff format .` |
| Lint (전체) | ruff check | `uv run ruff check .` |
| Lint auto-fix | ruff check --fix | `uv run ruff check --fix .` |
| 자동 강제 | pre-commit | `git commit` 시 자동 실행 |

설정은 `pyproject.toml` 의 `[tool.ruff.*]` 단일 출처.

## 2. 활성화된 lint 룰

`pyproject.toml` 의 `select`:

- `E`, `F` — pycodestyle errors, pyflakes
- `I` — isort (import 정렬)
- `B` — flake8-bugbear (흔한 버그 패턴)
- `UP` — pyupgrade (구식 문법)
- `SIM` — flake8-simplify (단순화 가능)
- `DTZ` — flake8-datetimez (naive datetime 차단)

새 룰을 추가하면 **codebase 전체에 한 번에 적용** 한 뒤 commit. 점진적 적용 금지 — diff 큰 PR 이 차라리 낫고, 일관성이 더 중요.

## 3. pre-commit 설치 (첫 clone 후 1회)

```bash
uv run pre-commit install
```

이후 `git commit` 마다 `ruff-format` → `ruff check --fix` 자동 실행. 위반이 있으면 commit 차단 (fix 가능한 건 자동 수정, stage 다시 필요).

## 4. 의도된 위반 — per-file ignore

`E402` (top-of-file import) 면제 파일:
- `demo.py`: `load_dotenv()` / LLM provider 조건부 import 패턴
- `packages/gateway/gateway/main.py`: `load_dotenv()` 후 모듈 import
- `alembic/env.py`: `sys.path` 조작 후 import

`DTZ` 면제:
- `packages/common/common/clock.py`: helper 정의 자체

새 파일이 이 패턴을 필요로 하면 **반드시 사유 코멘트와 함께** `[tool.ruff.lint.per-file-ignores]` 에 추가.

## 5. format 차이 PR 금지

format 차이만 있는 commit/PR 은 만들지 말 것. format 변경이 필요하면 별도 commit 으로 일괄 적용하고 다시는 손대지 않는다. 기능 commit 에 format 노이즈 섞이면 리뷰가 폭발.

```bash
uv run ruff format .   # 의심되면 먼저 실행
```
