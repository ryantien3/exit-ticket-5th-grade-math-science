# Exit Ticket API

FastAPI service for the Exit Ticket platform. Python 3.12, SQLAlchemy, Postgres.

## Run locally

From `app/api`:

```bash
# One-time setup
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt      # also installs requirements.txt
cp .env.example .env

# Start Postgres (compose.yaml lives in the repo root)
docker compose -f ../../compose.yaml up -d db

# Run the API (auto-reloads when code changes)
uvicorn app.main:app --reload
```

The API is at http://127.0.0.1:8000, with interactive docs at http://127.0.0.1:8000/docs.

```bash
curl -i http://127.0.0.1:8000/healthz   # 200: process is alive
curl -i http://127.0.0.1:8000/readyz    # 200 if Postgres is reachable, 503 if not
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | Liveness: the process is running |
| GET | `/readyz` | Readiness: the database is reachable |
| GET | `/standards` | All standards |
| GET | `/exit-tickets` | Ticket summaries |
| GET | `/exit-tickets/{id}` | A ticket's questions, without answers or explanations |
| POST | `/exit-tickets/{id}/submissions` | Grade and save a student's answers (all questions required) |
| GET | `/classrooms/{id}/mastery` | Percent correct and mastery level per standard |

After each saved submission, `app/events.py` publishes one event per answer
(logged as JSON for now; Pub/Sub in Phase 6).

## Tests

Most tests need Postgres running (`docker compose -f ../../compose.yaml up -d db`).
They use a separate `exitticket_test` database, created, migrated, and seeded
automatically; your dev data is never touched. Each test is rolled back afterwards.

```bash
pytest                                          # everything
pytest tests/test_health.py tests/test_grading.py   # the tests that don't need Postgres
```

Set `TEST_DATABASE_URL` to use a different test database.

## Database migrations (Alembic)

No migrations exist yet. Once tables are added:

```bash
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

## Configuration

Read from environment variables; `.env` is a local-only fallback and is gitignored.

| Variable | Required | Default | Example |
|---|---|---|---|
| `DATABASE_URL` | yes | (none) | `postgresql+psycopg://exitticket:localdev@localhost:5432/exitticket` |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `ENVIRONMENT` | no | `local` | `local`, `dev`, `prod` |

## Stopping

```bash
# Ctrl+C stops uvicorn, then:
docker compose -f ../../compose.yaml down       # stop Postgres, keep its data
docker compose -f ../../compose.yaml down -v    # stop Postgres AND delete its data
deactivate                                      # leave the virtual environment
```
