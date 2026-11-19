"""Entry point: uvicorn loads `app` from this module (uvicorn app.main:app)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import events
from app.config import get_settings
from app.logging_config import configure_logging
from app.routes import classrooms, exit_tickets, health, me, sessions, standards

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Exit Ticket API started (environment=%s)", settings.environment)
    # In the worker process, after any fork: one Pub/Sub client for the life of the process.
    events.start_publisher(settings.pubsub_topic)
    yield
    logger.info("Exit Ticket API shutting down")
    # On SIGTERM (a rollout, a scale-down): send what's still batched before the process exits.
    events.stop_publisher()


app = FastAPI(title="Exit Ticket API", lifespan=lifespan)
app.include_router(health.router)
app.include_router(standards.router)
app.include_router(sessions.router)
app.include_router(me.router)
app.include_router(exit_tickets.router)
app.include_router(classrooms.router)
