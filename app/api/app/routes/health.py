"""Health endpoints for Kubernetes probes.

/healthz -> liveness probe. If it fails, Kubernetes restarts the container.
            It deliberately does NOT check the database: a database outage should
            not cause every pod to restart in a loop.
/readyz  -> readiness probe. If it fails, Kubernetes stops sending traffic to the
            pod (but leaves it running) until the database is reachable again.
"""

import logging
from collections.abc import Callable

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from app.db import check_database

logger = logging.getLogger(__name__)

router = APIRouter()


def get_database_check() -> Callable[[], None]:
    # Wrapped in a dependency so tests can swap in a fake check.
    return check_database


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
def readyz(database_check: Callable[[], None] = Depends(get_database_check)) -> JSONResponse:
    try:
        database_check()
    except Exception:
        # Full details go to the logs; callers only see a generic status.
        logger.exception("Readiness check failed: database unreachable")
        return JSONResponse(status_code=503, content={"status": "unavailable"})
    return JSONResponse(status_code=200, content={"status": "ready"})
