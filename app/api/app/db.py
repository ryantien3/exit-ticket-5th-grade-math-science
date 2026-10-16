"""Database connection.

The engine is created on first use, not at import time, and creating it does not
open a connection. That keeps the app (and the tests) startable without Postgres.
"""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from app.config import get_settings


@lru_cache
def get_engine() -> Engine:
    return create_engine(
        get_settings().database_url,
        # Test each pooled connection before using it, in case the database restarted.
        pool_pre_ping=True,
        # Give up after 3 seconds instead of hanging if Postgres is unreachable.
        connect_args={"connect_timeout": 3},
    )


def get_session() -> Iterator[Session]:
    """FastAPI dependency: one database session per request, closed afterwards."""
    with Session(get_engine()) as session:
        yield session


def check_database() -> None:
    """Raise an exception if the database cannot answer a trivial query."""
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
