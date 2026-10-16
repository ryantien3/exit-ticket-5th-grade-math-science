"""Entry point: uvicorn loads `app` from this module (uvicorn app.main:app)."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.logging_config import configure_logging
from app.routes import classrooms, exit_tickets, health, standards

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Exit Ticket API started (environment=%s)", settings.environment)
    yield
    logger.info("Exit Ticket API shutting down")


app = FastAPI(title="Exit Ticket API", lifespan=lifespan)
app.include_router(health.router)
app.include_router(standards.router)
app.include_router(exit_tickets.router)
app.include_router(classrooms.router)
