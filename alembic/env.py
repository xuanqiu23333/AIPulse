"""Run reviewed migrations with a dedicated, explicitly selected account."""

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

from aipulse.config import load_database_settings
from aipulse.models import Base
from aipulse.storage.migrations import migration_url


config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Render SQL for review without loading credentials or opening a socket."""
    context.configure(
        dialect_name="mysql",
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    """Bind the synchronous Alembic context through AsyncConnection."""
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Connect only after selecting and validating dedicated migration credentials."""
    settings = load_database_settings()
    url = migration_url(
        settings,
        target=os.environ.get("AIPULSE_MIGRATION_TARGET", ""),
        user=os.environ.get("AIPULSE_MIGRATION_USER"),
        password=os.environ.get("AIPULSE_MIGRATION_PASSWORD"),
    )
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
elif config.attributes.get("connection") is not None:
    # A guarded caller has verified this exact connection before any DDL.
    do_run_migrations(config.attributes["connection"])
else:
    asyncio.run(run_async_migrations())
