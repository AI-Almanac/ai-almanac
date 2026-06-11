"""Alembic environment for ai-almanac.

Uses a sync engine so migrations work whether they're invoked from the CLI
(`alembic upgrade head`) or from inside the FastAPI lifespan event handler
(where an asyncio loop is already running).
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from ai_almanac.server.database_urls import sync_database_url

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = None


def _sync_url() -> str:
    """Return a sync SQLAlchemy URL derived from the configured database URL."""
    from ai_almanac.settings import settings

    return sync_database_url(settings.resolve_database_url())


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_sync_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
