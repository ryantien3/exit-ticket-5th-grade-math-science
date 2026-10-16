"""Shared test setup.

Database tests use a separate exitticket_test database (never your dev data):
it's created if needed, migrated with Alembic, and seeded once per test run.
Each test then runs inside a transaction that is rolled back afterwards, so
tests can't affect each other. Health tests don't need Postgres at all.
"""

import os
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://exitticket:localdev@localhost:5432/exitticket_test",
)
# Forced (not setdefault), before the app is imported, so a DATABASE_URL in your
# shell or .env can never point the tests at your dev database.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# Make scripts/seed.py importable, so tests load exactly the same seed data.
sys.path.insert(0, str(API_DIR.parent.parent / "scripts"))

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, make_url, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app import events  # noqa: E402
from app.db import get_engine, get_session  # noqa: E402
from app.main import app  # noqa: E402
from seed import seed  # noqa: E402


def create_test_database() -> None:
    url = make_url(TEST_DATABASE_URL)
    # CREATE DATABASE has to be run from a different database, outside a transaction.
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as connection:
            exists = connection.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database}
            )
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    except OperationalError:
        pytest.fail(
            f"Can't reach Postgres at {url.render_as_string(hide_password=True)}. "
            "Start it from the repo root with: docker compose up -d db",
            pytrace=False,
        )
    finally:
        admin.dispose()


@pytest.fixture(scope="session")
def database():
    create_test_database()
    alembic_config = Config(str(API_DIR / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(API_DIR / "alembic"))
    command.upgrade(alembic_config, "head")
    engine = get_engine()
    with engine.begin() as connection:
        seed(connection)
    return engine


@pytest.fixture
def db_session(database):
    connection = database.connect()
    transaction = connection.begin()
    # "create_savepoint": when endpoint code calls commit(), only a SAVEPOINT inside
    # the outer transaction is committed, and the outer one is rolled back below.
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def published_events(monkeypatch):
    """Replaces the real publisher with one that records events in a list."""
    captured: list[events.AnswerEvent] = []
    monkeypatch.setattr(events, "publish_answer_event", captured.append)
    return captured
