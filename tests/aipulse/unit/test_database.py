"""Async session setup stays lazy and keeps database targets isolated."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from aipulse.config import DatabaseSettings
from aipulse.storage.db import create_session_factory, create_test_engine


def test_test_session_can_open_and_close_without_connecting():
    settings = DatabaseSettings(
        _env_file=None,
        db_password="fake-dev-secret",
        test_db_name="aipulse_test_db",
        test_db_user="aipulse_test",
        test_db_password="fake-test-secret",
    )
    engine = create_test_engine(settings)

    async def check():
        factory = create_session_factory(engine)
        async with factory() as session:
            assert isinstance(session, AsyncSession)
            assert session.bind is engine
        await engine.dispose()

    asyncio.run(check())
