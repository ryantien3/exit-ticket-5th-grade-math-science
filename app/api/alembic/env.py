"""Alembic migration environment: connects Alembic to our settings."""

from alembic import context
from sqlalchemy import create_engine

from app.config import get_settings
from app.logging_config import configure_logging
from app.models import Base

settings = get_settings()
configure_logging(settings.log_level)

# Lets "alembic check" and "alembic revision --autogenerate" compare the models
# against the database.
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Write migration SQL to stdout instead of running it (alembic upgrade --sql)."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect to the database and run migrations."""
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
