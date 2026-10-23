# Exit Ticket API image. Build for GKE's CPU architecture (from the repo root):
#   docker buildx build --platform linux/amd64 -t exit-ticket-api:dev --load app/api

# Pinned to an exact Python patch and Debian release, so rebuilds are repeatable.
FROM python:3.12.15-slim-trixie

# Unbuffered: log lines reach stdout (and Cloud Logging) immediately.
# No .pyc files: the code is read-only to the app user, and the container is throwaway.
# No pip cache, version check or root warning: smaller image, quieter build.
# (Installing as root is fine here: this Python isn't managed by apt, and the app runs as UID 10001.)
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

# A system user with a fixed numeric UID, so Kubernetes' runAsNonRoot can verify it.
RUN useradd --system --user-group --uid 10001 --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Dependencies first: this layer is reused until requirements.txt changes.
# psycopg[binary] bundles libpq, so no apt packages are needed.
COPY requirements.txt .
RUN pip install -r requirements.txt

# App code and migrations. Owned by root, so the app user can read but not modify them.
# Migrations ship in the image but are run by a separate Job, never on startup.
COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

USER 10001

EXPOSE 8000

# Exec form: uvicorn is PID 1 and receives Kubernetes' SIGTERM for a graceful shutdown.
# 0.0.0.0: listen on the container's network interface, not just its own loopback.
# No HEALTHCHECK: Kubernetes ignores it and uses its probes on /healthz and /readyz.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
