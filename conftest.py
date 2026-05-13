"""Root conftest — 모든 테스트에 in-memory SQLite 강제 적용.

매 테스트 시작 시 캐시된 엔진/스토어를 폐기하여 격리를 보장한다.
스키마 생성은 각 테스트 또는 lifespan 에서 수행한다 (단순 인메모리 테스트는 conftest 의 schema fixture 사용 가능).
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

    # gateway.main 의 top-level `load_dotenv()` 가 .env 의 webhook secret 을
    # 환경에 주입한다. 이를 무력화하려면 import 이후에 delenv 해야 한다.
    import gateway.main  # noqa: F401 — side-effect import (load_dotenv 트리거)

    for var in ("SENTRY_CLIENT_SECRET", "WARROOM_DATADOG_TOKEN"):
        monkeypatch.delenv(var, raising=False)

    from gateway import store as store_mod
    from gateway.db import session as session_mod

    session_mod.reset_engine()
    store_mod.reset_store()
    yield
    session_mod.reset_engine()
    store_mod.reset_store()
