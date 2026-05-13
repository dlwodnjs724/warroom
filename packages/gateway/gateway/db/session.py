"""Async SQLAlchemy 엔진 / 세션 관리.

DATABASE_URL 환경변수로 백엔드 선택:
    dev/prod : mysql+aiomysql://user:pass@host:3306/dbname
    test/ci  : sqlite+aiosqlite:///:memory:
    default  : sqlite+aiosqlite:///./data/warroom.db  (로컬 첫 실행 편의)
"""

import os

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_DEFAULT_URL = "sqlite+aiosqlite:///./data/warroom.db"

_engine: AsyncEngine | None = None
_factory: async_sessionmaker[AsyncSession] | None = None


def current_url() -> str:
    return os.getenv("DATABASE_URL", _DEFAULT_URL)


def is_sqlite_backend() -> bool:
    """SQLite 백엔드 여부. dev/test 자동 스키마 생성을 분기하는 데 사용한다."""
    return current_url().startswith("sqlite")


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(current_url(), echo=False, pool_pre_ping=True)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _factory
    if _factory is None:
        _factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    return _factory


def reset_engine() -> None:
    """테스트가 in-memory DB 로 격리하기 위해 캐시된 엔진을 폐기한다."""
    global _engine, _factory
    _engine = None
    _factory = None


async def init_schema() -> None:
    """현재 엔진에 metadata.create_all 을 실행한다.

    SQLite (dev/test) 자동 셋업 전용. MySQL 운영에서는 호출하지 말고 `alembic upgrade head` 사용.
    """
    from gateway.db.models import Base

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
