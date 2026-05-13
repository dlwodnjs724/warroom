"""Gateway 패키지 테스트용 — async 스키마 생성 fixture 제공."""
import pytest_asyncio


@pytest_asyncio.fixture
async def schema():
    """in-memory sqlite 에 metadata.create_all 을 적용한다."""
    from gateway.db.session import init_schema

    await init_schema()
    yield
