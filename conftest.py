"""Root conftest — 모든 테스트에 in-memory SQLite 강제 적용.

매 테스트 시작 시 캐시된 엔진/repository 를 폐기하여 격리를 보장한다.
스키마 생성은 각 테스트 또는 lifespan 에서 수행한다 (단순 인메모리 테스트는 conftest 의 schema fixture 사용 가능).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    # gateway.main 의 top-level `load_dotenv()` 가 .env 의 webhook secret 을
    # 환경에 주입한다. 이를 무력화하려면 import 이후에 delenv 해야 한다.
    import gateway.main  # noqa: F401 — side-effect import (load_dotenv 트리거)

    for var in (
        "SENTRY_CLIENT_SECRET",
        "WARROOM_DATADOG_TOKEN",
        "GITHUB_REPO",
        "GITHUB_APP_ID",
        "GITHUB_APP_PRIVATE_KEY_PATH",
        "GITHUB_INSTALLATION_ID",
        "SLACK_BOT_TOKEN",
        "SLACK_CHANNEL",
        "SLACK_SIGNING_SECRET",
        "SENTRY_DSN",  # warroom self-monitoring — 테스트가 실 Sentry 호출 금지
        "SENTRY_AUTH_TOKEN",  # Phase 6.2 Sentry tool — 테스트가 실 API 호출 금지
        "SENTRY_ORG",
    ):
        monkeypatch.delenv(var, raising=False)

    from gateway import dependencies as deps_mod
    from gateway.infrastructure.db import repository as repo_mod
    from gateway.infrastructure.db import session as session_mod

    session_mod.reset_engine()
    repo_mod.reset_repository()
    deps_mod.reset_github_client()
    deps_mod.reset_slack_notifier()
    deps_mod.reset_sentry()
    yield
    session_mod.reset_engine()
    repo_mod.reset_repository()
    deps_mod.reset_github_client()
    deps_mod.reset_slack_notifier()
    deps_mod.reset_sentry()
