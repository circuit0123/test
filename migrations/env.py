"""Alembic environment: how migrations connect to the database.

The database URL comes from app settings (DATABASE_URL) unless the caller set
`sqlalchemy.url` explicitly (the tests do, to target the test database).
"""

import asyncio
from logging.config import fileConfig

from geoalchemy2 import alembic_helpers
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import get_settings
from app.db import models  # noqa: F401  (registers every table on Base.metadata)
from app.db.base import Base

config = context.config

# Callers that already set up logging (the test suite) pass configure_logger=False.
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # Ignore tables that exist in the database but not in our models, such as
    # PostGIS's own spatial_ref_sys; autogenerate would otherwise try to drop them.
    if type_ == "table" and reflected and compare_to is None:
        return False
    return alembic_helpers.include_object(obj, name, type_, reflected, compare_to)


def _configure(**kwargs) -> None:
    context.configure(
        target_metadata=target_metadata,
        include_object=include_object,
        render_item=alembic_helpers.render_item,  # renders PostGIS column types
        compare_type=True,
        **kwargs,
    )


def run_migrations_offline() -> None:
    """`alembic upgrade --sql`: print the SQL instead of running it."""
    _configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    _configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
