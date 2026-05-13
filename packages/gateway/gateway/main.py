"""Event Gateway — composition root.

FastAPI 앱을 조립한다. 책임 분담:
    - api/         : HTTP 라우터 (webhooks, incidents)
    - services/    : 유스케이스 (ingest, pipeline, decisions)
    - infrastructure/ : DB, monitors (외부 입력 어댑터), security, store
"""

import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()

from gateway.api.incidents import router as incidents_router
from gateway.api.webhooks import router as webhooks_router
from gateway.infrastructure.db.session import current_url, init_schema, is_sqlite_backend
from gateway.infrastructure.monitors.security import warn_if_secrets_missing
from gateway.services.pipeline import set_main_loop


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_main_loop(asyncio.get_running_loop())
    print("[WARROOM] Gateway 시작")
    warn_if_secrets_missing()
    if is_sqlite_backend():
        await init_schema()
        print(f"[WARROOM] SQLite 자동 스키마 셋업 완료 ({current_url()})")
    else:
        print(f"[WARROOM] DATABASE_URL={current_url()} — `alembic upgrade head` 가 선행되어야 합니다.")
    yield
    print("[WARROOM] Gateway 종료")
    set_main_loop(None)


app = FastAPI(title="Warroom Event Gateway", lifespan=lifespan)
app.include_router(webhooks_router)
app.include_router(incidents_router)
