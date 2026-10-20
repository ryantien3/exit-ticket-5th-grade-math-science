# Exit Ticket API

FastAPI service for the Exit Ticket platform. Python 3.12, SQLAlchemy, Postgres.

## Run locally

From `app/api`:

```bash
# One-time setup
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt      # also installs requirements.txt
cp .env.example .env                     # then set SESSION_SECRET and TEACHER_PASSCODE in .env

# Start Postgres (compose.yaml lives in the repo root), create the tables, load demo data
docker compose -f ../../compose.yaml up -d db
alembic upgrade head
python ../../scripts/seed.py

# Run the API (auto-reloads when code changes)
uvicorn app.main:app --reload
```

The API is at http://127.0.0.1:8000, with interactive docs at http://127.0.0.1:8000/docs.

```bash
curl -i http://127.0.0.1:8000/healthz   # 200: process is alive
curl -i http://127.0.0.1:8000/readyz    # 200 if Postgres is reachable, 503 if not
```

## Sign-in

Students sign in with their 6-character login code, teachers with the shared
`TEACHER_PASSCODE`. Both get back a token (a signed JWT) to send as
`Authorization: Bearer <token>`. In `/docs`, sign in, copy the `token`, and paste it into
the **Authorize** button. Teachers find each student's code at `/classrooms/{id}/students`.

## Endpoints

| Method | Path | Who | Purpose |
|---|---|---|---|
| GET | `/healthz` | anyone | Liveness: the process is running |
| GET | `/readyz` | anyone | Readiness: the database is reachable |
| GET | `/standards` | anyone | All standards |
| POST | `/student-sessions` | anyone | Sign in with a login code |
| POST | `/teacher-sessions` | anyone | Sign in with the teacher passcode |
| GET | `/me/exit-tickets` | student | Tickets released to my class, and whether I've submitted each |
| GET | `/exit-tickets/{id}` | student, teacher | A ticket's questions, without answers or explanations (students: released tickets only) |
| POST | `/exit-tickets/{id}/submissions` | student | Grade and save my answers (all questions required) |
| GET | `/exit-tickets` | teacher | Every ticket |
| GET | `/classrooms` | teacher | All classrooms |
| GET | `/classrooms/{id}/students` | teacher | Students, with their login codes |
| GET | `/classrooms/{id}/exit-tickets` | teacher | Every ticket, released or hidden for this class |
| PUT / DELETE | `/classrooms/{id}/exit-tickets/{ticket_id}/release` | teacher | Release / hide a ticket for this class |
| GET | `/classrooms/{id}/mastery` | teacher | Percent correct and mastery level per standard |
| GET | `/classrooms/{id}/mastery/students` | teacher | The same, per student |
| GET | `/classrooms/{id}/recent-submissions` | teacher | Latest submissions (`?limit=1-50`, default 10) |

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

Migrations live in `alembic/versions/`.

```bash
alembic current                  # which migration the database is at
alembic upgrade head             # apply any new ones
alembic check                    # confirm the models in app/models.py match the migrations
alembic revision --autogenerate -m "describe the change"   # draft a new one, then review it
```

## Configuration

Read from environment variables; `.env` is a local-only fallback and is gitignored.

| Variable | Required | Default | Example |
|---|---|---|---|
| `DATABASE_URL` | yes | (none) | `postgresql+psycopg://exitticket:localdev@localhost:5432/exitticket` |
| `LOG_LEVEL` | no | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `ENVIRONMENT` | no | `local` | `local`, `dev`, `prod` |
| `SESSION_SECRET` | yes | (none) | 32+ random characters; signs sign-in tokens |
| `TEACHER_PASSCODE` | yes | (none) | 8+ characters |

## Stopping

```bash
# Ctrl+C stops uvicorn, then:
docker compose -f ../../compose.yaml down       # stop Postgres, keep its data
docker compose -f ../../compose.yaml down -v    # stop Postgres AND delete its data
deactivate                                      # leave the virtual environment
```
