"""Event Gateway — composition root.

FastAPI 앱을 조립한다. 책임 분담:
    - api/         : HTTP 라우터 (webhooks, incidents)
    - services/    : 유스케이스 (ingest, pipeline, decisions)
    - infrastructure/ : DB, monitors (외부 입력 어댑터), security, store
"""

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from common.logging import configure_logging
from dotenv import load_dotenv
from fastapi import FastAPI

load_dotenv()
configure_logging()

logger = logging.getLogger(__name__)

from gateway.api.incidents import router as incidents_router
from gateway.api.slack import router as slack_router
from gateway.api.system import router as system_router
from gateway.api.webhooks import router as webhooks_router
from gateway.dependencies import (
    get_datadog_token,
    get_github_client,
    get_github_repo,
    get_sentry_secret,
    get_slack_notifier,
    get_slack_signing_secret,
    init_sentry,
    reset_github_client,
    reset_slack_notifier,
)
from gateway.infrastructure.db.repository import get_repository
from gateway.infrastructure.db.session import current_url, init_schema, is_sqlite_backend
from gateway.services.pipeline import drain_in_flight, set_main_loop
from gateway.services.security import warn_if_secrets_missing


@asynccontextmanager
async def lifespan(app: FastAPI):
    set_main_loop(asyncio.get_running_loop())
    logger.info("Gateway 시작")
    if init_sentry():
        logger.info("Sentry self-monitoring 활성화 (SENTRY_DSN 감지)")
    warn_if_secrets_missing(
        sentry_secret=get_sentry_secret(),
        datadog_token=get_datadog_token(),
        slack_signing_secret=get_slack_signing_secret(),
    )
    if is_sqlite_backend():
        await init_schema()
        logger.info("SQLite 자동 스키마 셋업 완료 (%s)", current_url())
    else:
        logger.info(
            "DATABASE_URL=%s — `alembic upgrade head` 가 선행되어야 합니다.",
            current_url(),
        )

    # 비정상 종료로 ANALYZING 에서 stuck 된 incident 정리 → FAILED.
    recovered = await get_repository().recover_stale_analyzing()
    if recovered:
        logger.info("stale ANALYZING 인시던트 %d건 → FAILED 마킹", recovered)

    # GitHub client 를 lifespan 진입 시 1회 생성 → 캐싱. 이후 services 는
    # dependencies.get_github_client() / get_github_repo() 만 의존.
    reset_github_client()
    get_github_client()
    logger.info("GitHub client 초기화 (GITHUB_REPO=%s)", get_github_repo() or "미설정")

    # Slack interactivity 전용 notifier — github 패턴 동일 (reset → eager init).
    # 미리 1회 생성해 env credential snapshot 을 lifespan boundary 에 고정한다.
    reset_slack_notifier()
    get_slack_notifier()

    yield
    logger.info("Gateway 종료 — in-flight pipeline drain")
    grace = float(os.getenv("WARROOM_SHUTDOWN_GRACE_SECONDS", "30.0"))
    done, cancelled = await drain_in_flight(timeout=grace)
    if done or cancelled:
        logger.info("drain — 정상 %d건 / cancel+FAILED %d건", done, cancelled)
    set_main_loop(None)
    reset_github_client()
    reset_slack_notifier()


app = FastAPI(title="Warroom Event Gateway", lifespan=lifespan)
app.include_router(webhooks_router)
app.include_router(incidents_router)
app.include_router(slack_router)
app.include_router(system_router)
