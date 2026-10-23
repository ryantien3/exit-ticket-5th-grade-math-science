# Phase 2 — The Application (API + Web)

Phase 2 builds the actual product, running entirely on a laptop: a Python API, a Postgres database, a student web app
and a teacher dashboard. Nothing touches Google Cloud yet, but every design choice here is made with GKE, Cloud SQL
and Pub/Sub in mind. The ACE exam notes at the end connect each local piece to the GCP service it becomes.

| Part | What was built | PR |
|---|---|---|
| **2A** | FastAPI service, data model, Alembic migration `0001`, seed data, grading, health checks, JSON logging, tests | #5 |
| **2B** | Classroom reporting endpoints, sign-in (login codes + JWT), exit ticket releases, migration `0002`, student web app, teacher dashboard, nginx | #7 |

> **About the commands in this doc.** Each code block contains commands only, no `#` comments. In zsh (the macOS
> default shell), `#` is **not** a comment unless `setopt interactivecomments` is on, so a pasted `# note` becomes
> extra arguments. This created stray folders and files three times in this project (see
> [Lessons learned](#15-lessons-learned)).

> **Placeholders used below.** Replace these with your own values when you run a command. Never commit the real
> values; the secrets live only in `app/api/.env`, which is gitignored.
>
> | Placeholder | Meaning |
> |---|---|
> | `YOUR_TEACHER_PASSCODE` | The `TEACHER_PASSCODE` you set in `app/api/.env` |
> | `STUDENT_CODE` | A student's 6-character login code (teachers see them at `/classrooms/{id}/students`) |
> | `TEACHER_TOKEN`, `STUDENT_TOKEN` | The `token` returned by a sign-in endpoint |
> | `CLASSROOM_ID`, `TICKET_ID`, `QUESTION_ID` | UUIDs from the list endpoints |

**Contents**

1. [Architecture](#1-architecture)
2. [Local development environment](#2-local-development-environment)
3. [Data model](#3-data-model)
4. [Migrations with Alembic](#4-migrations-with-alembic)
5. [Seed data](#5-seed-data)
6. [API design with FastAPI](#6-api-design-with-fastapi)
7. [SQL patterns worth knowing](#7-sql-patterns-worth-knowing)
8. [Sign-in and authorization](#8-sign-in-and-authorization)
9. [Health checks, logging and events](#9-health-checks-logging-and-events)
10. [Testing](#10-testing)
11. [The web apps](#11-the-web-apps)
12. [nginx as a reverse proxy](#12-nginx-as-a-reverse-proxy)
13. [Known limitations](#13-known-limitations)
14. [Interview talking points](#14-interview-talking-points)
15. [Lessons learned](#15-lessons-learned)
16. [ACE exam notes](#ace-exam-notes)

---

## 1. Architecture

**Locally, at the end of Phase 2:**

```
Browser
  │  http://localhost:8080
  ▼
nginx container ("web")  ── serves app/web/public (HTML, CSS, JS)
  │  /api/... → strips /api → http://host.docker.internal:8000/...
  ▼
uvicorn + FastAPI (your terminal, port 8000)
  │  postgresql+psycopg://...@localhost:5432
  ▼
Postgres 16 container ("db")  ── data in the named volume "pgdata"
```

**Where each piece goes in GCP (later phases):**

| Local | GCP target | Phase |
|---|---|---|
| nginx container | `web` Deployment on GKE Autopilot | 3 |
| uvicorn in a terminal | `api` Deployment on GKE Autopilot (image in Artifact Registry, built for `linux/amd64`) | 3 |
| Postgres container | Cloud SQL for PostgreSQL | 4 |
| `.env` file | Kubernetes env vars + **Secret Manager** for secrets | 3–4 |
| Answer events written to the log | **Pub/Sub** topic → **BigQuery** → Looker Studio | 6 |
| Shared teacher passcode | **Identity-Aware Proxy (IAP)** with Google sign-in | 3+ |

**Why the browser only ever talks to nginx:** all API calls use relative URLs like `/api/exit-tickets`, so the
browser sends them to whichever server served the page. That means:
- **no CORS setup** is needed, because the page and the API share one origin;
- **no API address is hardcoded** in the JavaScript;
- **nginx strips the `/api` prefix itself**, so on GKE the Ingress doesn't need path rewriting, which GKE's built-in
  Ingress can't do;
- the same files work on a laptop and on GKE.

Both of these are explained in [Why a reverse proxy: no CORS, no Ingress rewrites](#why-a-reverse-proxy-no-cors-no-ingress-rewrites).

---

## 2. Local development environment

### Python virtual environment

A **virtual environment** (venv) is a private copy of Python with its own installed packages, so this project's
versions never clash with another project's. It lives in `app/api/.venv` and is gitignored.

```bash
cd app/api
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pip check
```

- `requirements.txt` holds **runtime** dependencies, **pinned to exact versions** (`fastapi==0.143.2`), so every
  install and every container build gets the same code.
- `requirements-dev.txt` adds test tools and includes the runtime file with `-r requirements.txt`.
- `.venv/bin/python` (or `.venv/bin/pytest`) runs the venv's Python without activating anything. That's the safest
  habit: plain `python` might be a different Python without your packages.
- VS Code needs to be pointed at the venv too: Cmd+Shift+P → "Python: Select Interpreter" → `app/api/.venv/bin/python`.

### Settings: environment variables first, `.env` as a fallback

[`app/config.py`](../app/api/app/config.py) uses **pydantic-settings**: each setting is a typed field, read from
environment variables, with `app/api/.env` as a local fallback. **Real environment variables always win.** That's
what lets GKE configure the app without any file.

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+psycopg://...` (`+psycopg` selects the psycopg v3 driver) |
| `LOG_LEVEL` | no | `INFO` by default |
| `ENVIRONMENT` | no | `local` by default |
| `SESSION_SECRET` | yes | at least 32 characters; signs sign-in tokens |
| `TEACHER_PASSCODE` | yes | at least 8 characters |

Design choices worth remembering:
- **No defaults for secrets.** A missing secret stops the app at startup instead of letting it run insecurely.
- **`SecretStr`** hides the value in logs and error messages (it prints as `**********`).
- **Placeholders are rejected.** `.env.example` is public, so the app refuses to start if a secret still starts with
  `replace-me`.
- **`.env` is gitignored; `.env.example` is committed.** The example file documents every setting without any real
  values.
- **`--reload` doesn't watch `.env`.** After editing it, restart uvicorn (Ctrl+C, then start it again).

Making a strong random secret:
```bash
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Docker Compose for Postgres and nginx

[`compose.yaml`](../compose.yaml) runs two services. The database:

- **`image: postgres:16`**: the major version is pinned, so an upgrade never happens by surprise.
- **`127.0.0.1:5432:5432`**: published on localhost only, so other devices on the Wi-Fi can't reach it.
- **The named volume `pgdata`** keeps the data across `docker compose down`. **`down -v` deletes the volume**, and
  with it all your data.
- **The `healthcheck`** runs `pg_isready` every 5 seconds. "Running" only means the container started; **"healthy"**
  means Postgres actually accepts connections.

```bash
docker compose up -d db web
docker compose ps
docker compose logs web --tail 20
docker compose stop web db
```

`up -d` starts the services in the background ("detached") and does nothing if they're already running. `stop` keeps
the containers and their data; `down` removes the containers, but the volume, and so the data, survives.

### Daily start-up

You need two terminal tabs. In the first, start the API and leave it running:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science/app/api
.venv/bin/uvicorn app.main:app --reload
```
In the second:
```bash
cd ~/projects/exit-ticket-5th-grade-math-science
docker compose up -d db web
```

| URL | What |
|---|---|
| http://localhost:8080 | Student app (through nginx) |
| http://localhost:8080/teacher.html | Teacher dashboard |
| http://localhost:8000/docs | Interactive API docs (served by FastAPI directly) |

The first time on a fresh database (or after pulling a new migration), also run these from `app/api` before
starting uvicorn:
```bash
.venv/bin/alembic upgrade head
.venv/bin/python ../../scripts/seed.py
```

### Stopping everything

1. In the uvicorn tab, press **Ctrl+C**.
2. Then, from the repo root, use **one** of these:

```bash
docker compose --profile "*" stop
```
```bash
docker compose --profile "*" down
```
```bash
docker compose --profile "*" down -v
```

| Command | Containers | Database data |
|---|---|---|
| `docker compose --profile "*" stop` | stopped, kept (fastest to start again) | kept |
| `docker compose --profile "*" down` | removed | kept (in the `pgdata` volume) |
| `docker compose --profile "*" down -v` | removed | **deleted**: re-run migrations and the seed afterwards |

> **Updated in Phase 3A.** `web` now belongs to the `dev` compose profile, and a plain `docker compose stop` skips
> services in profiles, so it would leave nginx running. `--profile "*"` includes every profile. See
> [Phase 3A](phase-3a-containers.md#3-compose-profiles-two-modes-one-file).

Nothing in Phase 2 runs in the cloud, so stopping costs nothing and there's no cloud teardown to do.

---

## 3. Data model

```
standards ─┐
           ├── questions ──┐
           │               ├── exit_ticket_questions ── exit_tickets ──┐
           │               │                                           │
classrooms ┼── students ───┼── answers                                 │
           └───────────────┴── classroom_exit_tickets (releases) ──────┘
```

| Table | Key | Notes |
|---|---|---|
| `standards` | `code` (e.g. `NY-5.NF.1`) | `domain` (`NY-5.NF`), `subject` (`math`/`science`), description |
| `questions` | `id` (UUID) | 4 choices in JSONB, `correct_choice` A–D, explanation, `standard_code` |
| `exit_tickets` | `id` | title, subject |
| `exit_ticket_questions` | (`ticket_id`, `question_id`) | `position`, unique per ticket |
| `classrooms` | `id` | unique `name` |
| `students` | `id` | synthetic `display_name`; secret `login_code` (unique, format-checked) |
| `answers` | `id` | one row per question answered; `is_correct` decided **by the server** |
| `classroom_exit_tickets` | (`classroom_id`, `ticket_id`) | a row means "released to this class"; `released_at` |

**Constraints do the guarding, not just the code.** The database rejects bad data even if an endpoint has a bug:

- **Check constraints:** `subject IN ('math','science')`; `correct_choice IN ('A','B','C','D')`; exactly 4 choices;
  `position >= 1`; `login_code ~ '^[A-HJKMNP-Z2-9]{6}$'`.
- **A composite foreign key** `answers(ticket_id, question_id) → exit_ticket_questions`: an answer can only be saved
  for a question that's really on that ticket.
- **A copied column, kept honest.** `answers.standard_code` is copied from the question, so analytics can group by
  standard without a join. A second composite FK, `(question_id, standard_code) → questions(id, standard_code)`,
  guarantees the copy can never disagree with the original.
- **Unique `(student_id, ticket_id, question_id)`:** one submission per student per ticket. It also catches two
  identical submissions that race each other; the API turns that error into a 409.
- **A naming convention** (`pk_`, `fk_`, `uq_`, `ck_`, `ix_`) gives every constraint a predictable name, which
  migrations need in order to drop one later.

**Other choices:**
- **UUID primary keys:** IDs can be generated anywhere (the API, the seed script) without asking the database first.
- **`timestamptz`:** stores the exact moment, in UTC.
- **No submissions table.** A "submission" is all of one student's answers for one ticket; the unique rule above
  makes that grouping unambiguous.

### Why `standard_code` is stored on each answer

Strictly speaking, it's duplicate data: the question already knows its standard. Copying it onto every answer row
(a deliberate **denormalization**) pays off three ways:

1. **The most common question is cheap.** "How is this class doing on NY-5.NF.1?" becomes a `GROUP BY
   answers.standard_code`, with no join to `questions` on every dashboard refresh. An index on
   `(standard_code, answered_at)` supports it.
2. **The analytics pipeline needs it.** Each answer event sent to Pub/Sub, and later each BigQuery row, is
   **self-contained**: it already says which standard it measures. BigQuery is fastest with wide, flat tables, and
   Looker Studio can group by standard without joining back to Postgres.
3. **It can't go wrong.** Duplicated data normally risks drifting out of sync. Here a composite foreign key,
   `(question_id, standard_code) → questions(id, standard_code)`, makes the database itself reject an answer whose
   copy disagrees with its question.

The general lesson: normalize by default, and denormalize **on purpose**, for a read path you can name, with a
constraint that keeps the copy honest.

---

## 4. Migrations with Alembic

A **migration** is a versioned, reviewable script that changes the database's structure. The tables are only ever
created by migrations, never by `Base.metadata.create_all()`. That way every environment (laptop, test DB, Cloud SQL)
goes through the same steps, in the same order.

### Why migrations instead of `create_all()`

SQLAlchemy's `Base.metadata.create_all()` creates any table that's missing, straight from the models. It's tempting
for a first version, but it falls apart as soon as the app has real data:

| | `create_all()` | Alembic migrations |
|---|---|---|
| Changing an **existing** table (add a column, a constraint) | Can't. It only creates missing tables, and silently skips existing ones. | Each change is a script: `0002` added `login_code` to a table full of students |
| Existing rows | No way to fill in new columns | The script can backfill data (expand → backfill → contract) |
| History | None: you can't tell what state a database is in | `alembic_version` records exactly which migration each database is at |
| Undo | None | `downgrade` steps back |
| Review | Changes happen invisibly at app startup | Each change is a file in a pull request, reviewed like code |
| Multiple app copies | Every pod would race to change the schema at startup | Migrations run once, as a deployment step |

The test suite also builds its database with `alembic upgrade head`, not `create_all()`. So tests run against
exactly the schema production will have, and a broken migration fails the tests.

```
0001_create_core_tables  →  0002_login_codes_and_releases   (head)
```

Run these from `app/api`:
```bash
.venv/bin/alembic current
.venv/bin/alembic upgrade head
.venv/bin/alembic check
.venv/bin/alembic downgrade 0001
.venv/bin/alembic revision --autogenerate -m "describe the change"
```

| Command | What it does |
|---|---|
| `current` | Shows which migration the database is at; the version is stored in the `alembic_version` table |
| `upgrade head` | Applies every migration not yet applied |
| `check` | Confirms the models in `app/models.py` match the migrations exactly (use it in CI) |
| `downgrade <rev>` | Undoes migrations back to that revision. **Test it before you need it.** |
| `revision --autogenerate` | Drafts a migration by comparing models with the database. **Always read the draft before applying it.** |

### Adding a required column to a table that has data: expand, backfill, contract

You can't add a `NOT NULL` column to a table that already has rows, because those rows would break the rule
immediately. Migration `0002` does it in three steps:

1. **Expand:** add `login_code` as nullable. Existing rows get NULL.
2. **Backfill:** give every existing student a random, unique code.
3. **Contract:** make the column `NOT NULL`, then add the unique and check constraints.

This is the standard way to change a production schema without downtime.

**Migrations keep frozen copies of what they need.** `0002` has its own copy of the code alphabet instead of
importing `app/auth.py`. The app keeps changing, but a migration must do exactly what it did on the day it was
written.

---

## 5. Seed data

[`scripts/seed.py`](../scripts/seed.py) loads synthetic data: 9 standards, 45 questions, 6 exit tickets, 1 classroom
and 24 students. It's **safe to run again and again**:

- **Stable IDs:** each row's ID comes from `uuid5(namespace, "question:nf1-1")`, so the same key always produces the
  same UUID.
- **Upserts:** `INSERT ... ON CONFLICT DO UPDATE` updates an existing row in place instead of duplicating it.
- **`insert_only` columns** are set when a row is created and never overwritten afterwards. That's how re-seeding
  keeps each student's login code.
- **One transaction:** either everything is saved, or nothing is.
- **Random login codes.** They aren't derived from names, because this repo is public and anyone could reproduce
  them.

```bash
cd app/api
.venv/bin/python ../../scripts/seed.py
```

All student names are synthetic (first name + last initial). No real student data, ever.

---

## 6. API design with FastAPI

### How a request flows

```
request → router → dependencies (session, auth) → body validation (Pydantic) → endpoint → response model → JSON
```

- **Routers** group endpoints by area: `health`, `standards`, `sessions`, `me`, `exit_tickets`, `classrooms`.
- **Dependencies** (`Depends(...)`) are functions FastAPI runs before the endpoint: one database session per
  request, the signed-in student, a teacher-only check. Tests can swap them out with `app.dependency_overrides`.
- **Request models** (`SubmissionIn`) validate the body. `extra="forbid"` rejects unexpected fields, so a client
  can't send `is_correct` or `student_id`.
- **Response models work as an allowlist.** Only listed fields are sent. The student's view of a question
  (`StudentQuestion`) has **no** `correct_choice` or `explanation` field, so the answer key can't leak even by
  accident. A test checks every ticket for this.
- **Grading happens on the server.** The client sends letters; the server decides `is_correct`.

### Endpoints

| Method | Path | Who |
|---|---|---|
| GET | `/healthz`, `/readyz`, `/standards` | anyone |
| POST | `/student-sessions`, `/teacher-sessions` | anyone (sign-in) |
| GET | `/me/exit-tickets` | student |
| GET | `/exit-tickets/{id}` | student (released only) or teacher |
| POST | `/exit-tickets/{id}/submissions` | student |
| GET | `/exit-tickets` | teacher |
| GET | `/classrooms`, `/classrooms/{id}/students` (with login codes), `/classrooms/{id}/exit-tickets` | teacher |
| PUT / DELETE | `/classrooms/{id}/exit-tickets/{ticket_id}/release` | teacher |
| GET | `/classrooms/{id}/mastery`, `/mastery/students`, `/recent-submissions?limit=1-50` | teacher |

### Server-side grading and hidden answers

The browser is never trusted with the answer key:

- **What students receive:** `GET /exit-tickets/{id}` returns each question's prompt and choices, built from the
  `StudentQuestion` response model. That model has **no** `correct_choice` or `explanation` field, so they can't be
  sent even by mistake. Anything sent to a browser is visible in its developer tools, so "the page just doesn't show
  it" would not be enough. A test checks every ticket's response for answer-key fields and explanation text.
- **What students send:** only their chosen letters (`{"question_id": ..., "chosen_choice": "B"}`). `extra="forbid"`
  rejects anything else, such as `is_correct`.
- **Grading:** the **server** compares each letter with `correct_choice`, saves `is_correct`, and only then returns
  the score, the correct answers and the explanations.
- **Who submitted:** the student comes from the sign-in token, not the request body.
- **No retakes:** one submission per student per ticket; a second attempt gets 409.
- **Unreleased tickets** return 404 to students, so they can't preview questions by guessing ticket IDs.

### Trying the API with curl

These go straight to uvicorn on port 8000. Through nginx, use `http://localhost:8080/api/...` instead. Sign in as the
teacher and copy the `token` from the response:
```bash
curl -s -X POST http://localhost:8000/teacher-sessions -H "Content-Type: application/json" -d '{"passcode": "YOUR_TEACHER_PASSCODE"}'
```
List classrooms, then a class's students with their codes, then its exit tickets:
```bash
curl -s http://localhost:8000/classrooms -H "Authorization: Bearer TEACHER_TOKEN"
curl -s http://localhost:8000/classrooms/CLASSROOM_ID/students -H "Authorization: Bearer TEACHER_TOKEN"
curl -s http://localhost:8000/classrooms/CLASSROOM_ID/exit-tickets -H "Authorization: Bearer TEACHER_TOKEN"
```
Release a ticket to that class (use `-X DELETE` to hide it again):
```bash
curl -s -X PUT http://localhost:8000/classrooms/CLASSROOM_ID/exit-tickets/TICKET_ID/release -H "Authorization: Bearer TEACHER_TOKEN"
```
Sign in as a student, then see that student's released tickets and one ticket's questions:
```bash
curl -s -X POST http://localhost:8000/student-sessions -H "Content-Type: application/json" -d '{"login_code": "STUDENT_CODE"}'
curl -s http://localhost:8000/me/exit-tickets -H "Authorization: Bearer STUDENT_TOKEN"
curl -s http://localhost:8000/exit-tickets/TICKET_ID -H "Authorization: Bearer STUDENT_TOKEN"
```
Submit answers. The `answers` list needs one entry per question on the ticket:
```bash
curl -s -X POST http://localhost:8000/exit-tickets/TICKET_ID/submissions -H "Authorization: Bearer STUDENT_TOKEN" -H "Content-Type: application/json" -d '{"answers": [{"question_id": "QUESTION_ID", "chosen_choice": "B"}]}'
```
Then look at the teacher views:
```bash
curl -s http://localhost:8000/classrooms/CLASSROOM_ID/mastery -H "Authorization: Bearer TEACHER_TOKEN"
curl -s http://localhost:8000/classrooms/CLASSROOM_ID/mastery/students -H "Authorization: Bearer TEACHER_TOKEN"
curl -s "http://localhost:8000/classrooms/CLASSROOM_ID/recent-submissions?limit=5" -H "Authorization: Bearer TEACHER_TOKEN"
```
The quotes around the last URL matter: without them, zsh treats `?` as a filename wildcard. The interactive docs at
http://localhost:8000/docs do all of this with buttons: sign in, paste the token into **Authorize**, then
**Try it out** on any endpoint.

### HTTP status codes used

| Code | Meaning here |
|---|---|
| 200 / 201 / 204 | OK / created (a submission or a sign-in) / done, with no body (hiding a ticket) |
| 401 | **Who are you?** No token, or an expired, forged or edited one |
| 403 | **I know who you are, but no.** A student calling a teacher endpoint |
| 404 | Not found. **Also used for unreleased tickets**, so students can't tell which tickets exist |
| 409 | Conflict: this student already submitted this ticket |
| 422 | The request is malformed or incomplete (FastAPI's automatic validation, plus our own checks) |
| 502 / 504 | Sent by **nginx**: it couldn't reach the API |
| 503 | `/readyz`: the database is unreachable |

### Idempotency

`PUT .../release` and `DELETE .../release` are **idempotent**: doing either twice leaves things the same as doing it
once. A double-click or an automatic retry is harmless. `POST` is not idempotent, which is why the
one-submission-per-ticket rule (409) is needed.

### Mastery levels in one place

[`app/grading.py`](../app/api/app/grading.py) holds `MASTERED_AT = 80` and `APPROACHING_AT = 60`, plus `percent()`
and `mastery_level()`. Every endpoint that labels mastery calls these.

The level is decided from the **rounded** percent that's shown, so a displayed "80.0%" can never be labeled
"approaching". The teacher page shows percents with the same one decimal, for the same reason.

---

## 7. SQL patterns worth knowing

**1. Counting two things in one pass with `FILTER`:**
```sql
SELECT standard_code, count(*) AS answers, count(*) FILTER (WHERE is_correct) AS correct
FROM answers JOIN students ON students.id = answers.student_id
WHERE students.classroom_id = :classroom_id
GROUP BY standard_code;
```
Group by `(student_id, standard_code)` instead and you get the student × standard heatmap data.

**2. Selecting columns you didn't group by.** Normally SQL rejects this. Postgres allows it when you group by a table's
**primary key**, because the key determines the rest of that row:
```sql
SELECT students.id, students.display_name, exit_tickets.title, max(answers.answered_at) AS submitted_at
FROM answers JOIN students ... JOIN exit_tickets ...
GROUP BY students.id, exit_tickets.id
ORDER BY submitted_at DESC, students.display_name
LIMIT 10;
```
The second sort key is a **tie-breaker**, so the order is always the same.

**3. Outer join: ON versus WHERE.** This is the classic mistake:
```sql
SELECT exit_tickets.*, r.released_at, r.ticket_id IS NOT NULL AS released
FROM exit_tickets
LEFT JOIN classroom_exit_tickets r
  ON r.ticket_id = exit_tickets.id
 AND r.classroom_id = :classroom_id;
```
A `LEFT JOIN` keeps every ticket and fills in NULLs where there's no release row. If the classroom condition were in
`WHERE` instead, it would run **after** the join, and the NULL rows would fail it. Hidden tickets would vanish from
the list.

**4. Correlated subqueries as columns:**
```sql
SELECT exit_tickets.id,
       (SELECT count(*) FROM exit_ticket_questions q WHERE q.ticket_id = exit_tickets.id) AS question_count,
       EXISTS (SELECT 1 FROM answers a WHERE a.ticket_id = exit_tickets.id AND a.student_id = :me) AS submitted
FROM exit_tickets ...;
```
These subqueries refer to the outer row, so they run once per ticket. `EXISTS` stops at the first match. Joining
both child tables and grouping instead would **multiply rows** (4 questions × 4 answers = 16) and inflate the counts.

**5. Insert-if-missing:** `INSERT ... ON CONFLICT DO NOTHING` makes releasing idempotent and keeps the original
`released_at`.

**Two simple queries can beat one clever one.** The heatmap fetches the student list and the per-student counts
separately, then merges them in Python. That's clearer and easier to test than a single `LEFT JOIN` full of NULL
handling, and the speed difference is negligible at class size.

---

## 8. Sign-in and authorization

### Login codes (students)

- 6 characters from `ABCDEFGHJKMNPQRSTUVWXYZ23456789`: no 0/O or 1/I/L, so they're easy to copy from a card.
  That's 31⁶, about 887 million possible codes.
- Generated with Python's `secrets` module (cryptographic randomness), not `random`.
- Typing is forgiving: `" k7p-4qx "` is accepted as `K7P4QX`.
- **Kept separate from the student's UUID.** A UUID is too long to type, it already appears in URLs, logs and
  events, and it can never be changed. A code is used only as a secret, and it can be reset.
- **Stored as plain text on purpose.** Passwords are normally stored hashed (scrambled one way, so even the
  database owner can't read them), but teachers need to *read* these codes to hand them out. Because the codes are
  random, a leak means resetting codes, not a breach of reused passwords. Only teacher endpoints return them.
- A wrong code always gets the same 401 message, which reveals nothing about which codes exist.

### Teacher passcode

`TEACHER_PASSCODE` is compared with `secrets.compare_digest`, which takes the same time however many characters
match. A normal string comparison stops at the first wrong character, and that timing difference can be used to
guess a secret one character at a time (a **timing attack**).

### Tokens (JWT)

After signing in, the client receives a **JSON Web Token** and sends it with every request:
`Authorization: Bearer <token>`.

```
eyJhbGciOiJIUzI1NiJ9 . eyJzdWIiOiI…Iiwicm9sZSI6InN0dWRlbnQiLCJleHAiOjE3…} . 4f9a…signature
      header                         payload (claims)                          signature
```

- **The payload is only encoded, not encrypted.** Anyone can read it. Never put secrets in a JWT.
- **The signature is what protects it.** It's a hash of the header and payload, computed with `SESSION_SECRET`. If a
  student edits `"role": "student"` to `"teacher"`, the signature no longer matches and the API returns 401. A test
  does exactly this.
- **Claims used:** `sub` (who), `role` (`student`/`teacher`), `iat` (issued at), `exp` (expires). Students get 2
  hours, teachers 8.
- **Only HS256 is accepted** (`algorithms=["HS256"]`). This blocks the known attack where a token declares
  `"alg": "none"`, meaning no signature at all.
- **There's no server-side session table**, which makes tokens *stateless*. The trade-off: a token can't be revoked
  before it expires, except by rotating the secret, which signs everyone out.

### Enforcing roles

- `require_student` returns the signed-in `Student`, so the submission endpoint takes the student **from the token**.
  `student_id` was removed from the request body; before that, anyone could submit as anyone.
- `require_teacher` is attached to the **whole classrooms router**:
  `APIRouter(dependencies=[Depends(require_teacher)])`. Any endpoint added there later is protected automatically.
- Tests check **every** teacher endpoint for 401 without a token and 403 with a student token.

### Releases (pop quizzes)

Students only see, open and submit tickets released to **their** classroom. Hiding a ticket mid-quiz blocks new
submissions with a 404; answers already submitted are kept.

### Planned for GCP

| Now | Later |
|---|---|
| `.env` secrets | **Secret Manager**, read by the pod through Workload Identity (no key files) |
| Shared teacher passcode | **IAP** in front of the teacher pages: Google sign-in, enforced at the load balancer |
| No brute-force protection | **Cloud Armor** rate limiting on `POST /student-sessions` |

---

## 9. Health checks, logging and events

### Liveness and readiness

| Endpoint | Kubernetes probe | Checks | If it fails |
|---|---|---|---|
| `/healthz` | **liveness** | only that the process answers | Kubernetes **restarts** the container |
| `/readyz` | **readiness** | that the database is reachable (`SELECT 1`) | Kubernetes **stops sending traffic** to the pod, but leaves it running |

Liveness deliberately does **not** check the database. If it did, a database outage would make every pod restart in
a loop, which fixes nothing and makes recovery slower.

### Structured JSON logs

[`app/logging_config.py`](../app/api/app/logging_config.py) writes one JSON object per line to stdout. **Cloud
Logging** reads these automatically on GKE:
- the `severity` field sets each entry's log level;
- extra fields (`extra={"json_fields": {...}}`) become searchable fields instead of text buried in the message.

Uvicorn's own logs are routed through the same formatter.

### Answer events: the hand-off to analytics

After each saved submission, [`app/events.py`](../app/api/app/events.py) publishes one `AnswerEvent` per answer.
- **Today** that just means writing it to the log.
- **In Phase 6**, only the body of `publish_answer_event()` changes, to publish to Pub/Sub. The callers stay the same.
- **Events go out after the commit.** Analytics should never see answers that weren't actually saved.
- **A failed publish is logged, not returned as an error.** The student's answers are already saved.
- **`schema_version`** marks the event format as a contract with the pipeline: change fields deliberately, and bump
  the version when you do.

---

## 10. Testing

Run these from `app/api`, with the db container running:
```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -v tests/test_auth.py
.venv/bin/python -m pytest -x -k recent
```
`-q` is quiet, `-v` shows each test by name, `-k` picks tests whose name contains a word, and `-x` stops at the first
failure.

**How the test setup works** ([`tests/conftest.py`](../app/api/tests/conftest.py)):
1. Tests use a **separate database**, `exitticket_test`. It's created if needed, migrated with Alembic and seeded
   once per run. `DATABASE_URL` is **forced** to it, so a test can never touch your dev data.
2. **Secrets are forced to test-only values**, so tests never depend on, or reveal, your `.env`.
3. **Each test runs inside a transaction that's rolled back** afterwards. When endpoint code calls `commit()`, it only
   commits a SAVEPOINT inside that outer transaction (`join_transaction_mode="create_savepoint"`). Every test starts
   from the same clean data.
4. **Dependency overrides** swap in the test session, and a fake event publisher that records events in a list.
5. **Helpers:** `teacher_headers()`, `student_headers(i)`, `submit(...)`, and a `release` fixture.

**Reading the output:** `.` = passed, `F` = failed (an assert was false), `E` = error during setup (often "Can't
reach Postgres": start the db container). For a failure, read the test name, then the `>` line, then compare what
the code returned (left of `==`) with what was expected (right).

**What's covered (87 tests):** grading thresholds; the answer key never leaking; submission validation; 409 for
duplicates; publish failures; per-student mastery; the activity feed; releases; sign-in; and bad tokens (expired,
forged, edited, unsigned, garbage).

---

## 11. The web apps

Plain HTML, CSS and JavaScript **ES modules**: no framework and no build step, so the files nginx serves are exactly
the files in the repo.

```
app/web/public/
  index.html, teacher.html
  css/styles.css (shared), css/teacher.css
  js/api.js      fetch wrapper: tokens, 15 s timeout, ApiError kinds (unreachable/unauthorized/not_found/conflict)
  js/dom.js      $, el(), showScreen(), setStatus()
  js/student.js  student flow
  js/teacher.js  dashboard
```

### Security habits

- **API text is always set with `textContent`, never `innerHTML`.** Even a question containing `<script>` is shown
  as text, not run. This is how you prevent **XSS** (cross-site scripting).
- **Tokens live in `sessionStorage`**, which survives a refresh but is erased when the tab closes. That's right for
  shared Chromebooks. Student and teacher use **different storage keys**, so their sign-ins never mix.
- **Every storage access is wrapped in try/catch.** Some private-browsing modes block storage; the page still works.
- **Student codes on the dashboard are hidden** until the teacher asks to see them. Dashboards end up on projectors.

### Accessibility, for 10-year-olds on Chromebooks and iPads

- **Readable:** 20px base text, and every text color at least **7:1** against its background (WCAG AAA). The contrast
  was computed, not eyeballed.
- **Choices are real radio buttons** styled as big tiles (64px+). That keeps the right keyboard behavior: Tab into the
  group, arrow keys between choices, Space to pick, Enter for Next.
- **Clear focus:** one thick focus ring (dark outline + yellow halo) on everything you can Tab to.
- **Focus follows the screen.** When the screen changes, focus moves to its heading, so keyboard and screen-reader
  users know something happened.
- **Announcements:** a polite `aria-live` status line announces loading and errors.
- **Never color alone:** a correct/incorrect answer is a ✓/✗ plus words; a mastery level is color + symbol + label.
- **No timers.** Animations turn off for anyone with **reduce motion** enabled.

### Teacher dashboard and auto-refresh

- **Data on the page:** class mastery by standard (Math, then Science, grouped by domain); a student × standard
  heatmap (✓ ◐ ! + percent + a tinted cell, with a "needs support" filter and counts per row and column); recent
  activity; release/hide controls; student codes on request.
- **Heatmap colors** come from a validated **status palette** (good / warning / critical). It's checked for
  colorblind separation, and "needs support" has the darkest tint so it stands out.

The refresh loop:
```
refresh(): cancel the timer → fetch 4 endpoints in parallel → if a newer refresh started, discard
           → redraw (keeping keyboard focus) → "Last updated hh:mm:ss" → setTimeout(refresh, 15000)
```
- **`setTimeout` after finishing, not `setInterval`.** Requests can't pile up or arrive out of order on a slow
  network.
- **A refresh counter** discards results from a refresh that a newer one overtook, for example after switching
  classrooms.
- **The Page Visibility API** pauses refreshing while the tab is hidden and catches up the moment it's visible.
- **Pause / Refresh now buttons.** Content that updates on its own must be pausable (WCAG 2.2.2).
- **On failure,** the old data stays on screen with "Showing data from …", and it retries on schedule.

**Cost of polling:** about 4 requests every 15 seconds per open dashboard, whether or not anything changed. That's
fine for a few classrooms. See the ACE notes for the Pub/Sub version.

---

## 12. nginx as a reverse proxy

A **reverse proxy** receives the browser's requests and forwards some of them to another server. It returns that
server's answer as if it were its own.

### Why a reverse proxy: no CORS, no Ingress rewrites

**1. No CORS.** Browsers enforce the **same-origin policy**. An *origin* is scheme + host + **port**, so
`localhost:8080` and `localhost:8000` are different origins. If the page (from `:8080`) called the API (on `:8000`)
directly, the browser would block the responses unless the API sent **CORS** headers
(`Access-Control-Allow-Origin` and friends). Worse, every JSON `POST` or request with an `Authorization` header would
first trigger a **preflight** `OPTIONS` request: an extra round trip each time. CORS settings also differ per
environment and are easy to get dangerously wrong (`Allow-Origin: *`).

With nginx in front, the page and `/api/...` both come from `:8080`: **one origin, so CORS never comes up**. The API
has no CORS configuration at all.

**2. No path rewriting needed on GKE.** On GKE, internet traffic arrives through an **Ingress**. GKE's built-in
Ingress controller turns it into a Google **external Application Load Balancer**. An Ingress can route by path, for
example `/api/*` to the API's Service, but:
- the Kubernetes Ingress spec has **no rewrite field**;
- GKE's Ingress controller offers no rewrite annotation (unlike the community ingress-nginx controller's
  `rewrite-target`).

So the API would receive `/api/exit-tickets` and return 404, unless the API were changed to serve everything under
`/api` or rewriting were done somewhere else.

This design sidesteps all of that:
- The Ingress needs **one rule**: everything goes to the `web` Service.
- nginx, inside the web pod, serves the static files and proxies `/api/` to the API's internal Service, stripping the
  prefix exactly as it does on the laptop.
- **The API is never exposed to the internet.** Its Service can be `ClusterIP` (cluster-internal only): a smaller
  attack surface, and one load-balancer backend and health check instead of two.

(The newer **Gateway API** on GKE can rewrite paths, so it's an alternative. The nginx approach works the same with
either, and identically on a laptop.)

**The trade-off:** every API call takes one extra hop through nginx, and the web pods have to be scaled to carry API
traffic too. At this scale, both are negligible.

### The configuration

[`app/web/nginx.conf`](../app/web/nginx.conf), condensed:

```nginx
upstream api {
    server ${API_HOST} max_fails=0;
}
server {
    listen 80;
    server_tokens off;
    root /usr/share/nginx/html;
    location /api/ {
        proxy_pass http://api/;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_connect_timeout 5s;
        proxy_read_timeout 15s;
    }
    location / {
        try_files $uri $uri/ =404;
        add_header Cache-Control "no-cache";
    }
}
```

### The `proxy_pass` trailing-slash rule

What nginx sends to the API depends on whether `proxy_pass` has a **path after the address**. Even a lone `/` counts.

| Config | Browser asks | API receives |
|---|---|---|
| `proxy_pass http://api/;` | `/api/exit-tickets` | `/exit-tickets`: the matched `/api/` is **replaced** by `/` |
| `proxy_pass http://api;` | `/api/exit-tickets` | `/api/exit-tickets`: no path given, so it's **passed unchanged** (FastAPI 404) |
| `proxy_pass http://api/v2/;` | `/api/exit-tickets` | `/v2/exit-tickets` |

- **Keep the slashes matched:** `location /api/` with `http://api/`.
- **Variables turn the rule off.** If `proxy_pass` contains a variable (`proxy_pass $backend;`), the prefix is no
  longer replaced. That's why the address is filled in **before** nginx starts, using a template.

### Configuration through templates

The official nginx image processes files in `/etc/nginx/templates/*.template` when the container starts:
- `${VAR}` is replaced with that environment variable, and the result is saved in `/etc/nginx/conf.d/`;
- only variables that are actually set are replaced, so nginx's own `$host` and friends are left alone.

compose mounts `nginx.conf` as `default.conf.template` and sets `API_HOST=host.docker.internal:8000`. On GKE it will
be the API's Service name, e.g. `api:8000`. **One file, every environment.**

**Mounts are read-only (`:ro`),** and only `public/` is served. Serving the whole `app/web` folder would have
exposed `nginx.conf` at `/nginx.conf`.

### A real bug, and how it was found: `no live upstreams`

- **Symptom:** data requests sometimes returned 502 even though the API was running.
- **The clue:** `docker compose logs web` showed `no live upstreams`.
- **The cause:**
  1. `host.docker.internal` resolves to **two** addresses, IPv4 and IPv6. The IPv6 one is unreachable from the
     container.
  2. nginx treats a name with two addresses like a group of two servers.
  3. By default, nginx marks a server **down for 10 seconds** after one failed connection.

  So after the API restarted, students saw errors for up to 10 seconds.
- **A first fix that didn't work:** `extra_hosts: host-gateway`. Newer Docker Desktop maps that to both addresses
  too.
- **The working fix:** an explicit `upstream` with **`max_fails=0`**, meaning "never mark it down". It was tested
  with the API off and back on: 200 on the very next request.
- **Lesson:** look at the proxy's own logs first, and test the *recovery*, not just the happy path.

### Other directives

- **`host.docker.internal`:** from inside a container, it means "the Mac running Docker".
- **`proxy_connect_timeout` / `proxy_read_timeout`:** fail fast, so the page shows a friendly error instead of
  hanging.
- **`X-Forwarded-For` / `-Proto`:** tell the API who the real client was and whether they used http or https. They
  matter once a Google load balancer sits in front.
- **`Cache-Control: no-cache`:** the browser must check before reusing a cached file. Edits show up on refresh;
  unchanged files come back as a quick 304.

Changing `nginx.conf` needs a restart; changing HTML, CSS or JS only needs a browser refresh:
```bash
docker compose restart web
```

---

## 13. Known limitations

Phase 2 is a working product on a laptop, not a production system. These gaps are deliberate, and each has a planned
fix:

| Limitation | Why it's acceptable for now | Planned fix |
|---|---|---|
| **Basic sign-in:** one shared teacher passcode, no per-teacher accounts; every teacher can see every classroom | Enough to lock the teacher views down during development | **IAP** with Google identities in front of the teacher pages (Phase 3+) |
| **No brute-force protection** on student sign-in | About 887 million possible codes, and it only runs locally | **Cloud Armor** rate limiting on `POST /student-sessions` |
| **Tokens can't be revoked** before they expire (2 h students, 8 h teachers) | Short lifetimes limit the damage; rotating `SESSION_SECRET` signs everyone out | A server-side session list, if it's ever needed |
| **No "reset this student's code"** endpoint; codes stored as plain text | Teachers must be able to read codes; they're random, not reused passwords | A small teacher endpoint to issue a new code |
| **Polling, not real-time:** the dashboard refreshes every 15 seconds | Simple and robust; about 4 small requests per 15 s per open dashboard | **Pub/Sub** + Server-Sent Events (Phase 6) |
| **Synthetic data only:** 1 classroom, 24 made-up students, 45 seeded questions; no screens for creating classes or questions | The project rule: never real student data. The seed script is the only way data gets in. | Teacher authoring tools (not planned yet) |
| **Mastery is calculated live** in Postgres on every request; there's no history over time | Fast at class size | **BigQuery** + Looker Studio for history and trends (Phase 6) |
| **In-progress answers live only in the browser tab.** A refresh mid-ticket, or a sign-in expiring mid-quiz, loses them. | Exit tickets take minutes | Save answers as the student goes |
| **One submission per ticket**, no retakes | Matches how exit tickets are used | A retake policy, if teachers ask for one |
| **Runs only on a laptop:** no containers for the API, no CI, plain HTTP | That's what Phase 2 is | Containers + Artifact Registry + GKE + Cloud Build (Phase 3), HTTPS at the load balancer |
| Light theme only, English only | Scope | Later |

---

## 14. Interview talking points

Short, defensible stories for an interview, each one a decision plus its trade-off.

**The 30-second version.** "I built a classroom quiz platform for NYC 5th graders that tags every question with a New
York State standard, so teachers see mastery per standard in real time. It's a FastAPI + Postgres API with a plain
JavaScript front end behind nginx. I built it locally first, but cloud-shaped, so moving it to GKE Autopilot, Cloud
SQL and Pub/Sub is mostly packaging, not rewriting."

**1. "Local-first, but cloud-shaped."** Config comes only from environment variables, which become a ConfigMap and
Secret Manager on GKE. Logs are JSON lines with a `severity` field, which Cloud Logging understands. There are
separate liveness and readiness endpoints for Kubernetes probes. The front end uses relative URLs behind a reverse
proxy. Analytics events go through a single function that becomes a Pub/Sub publish later. *Why it matters to a
customer:* the expensive part of a cloud migration is usually untangling assumptions. Here they were designed out
up front.

**2. "Security by construction, not by remembering."**
- Response models are **allowlists**, so the answer key physically can't be serialized to a student.
- Grading happens on the server, and the student's identity comes from a **signed token**, never from the request
  body.
- Teacher authorization sits on the **router**, so a new endpoint is protected automatically, and tests check every
  teacher endpoint for 401 and 403.
- Unreleased quizzes return **404, not 403**, so their existence isn't revealed.
- The app refuses to start with missing or placeholder secrets.

**3. "I denormalized on purpose."** `standard_code` is copied onto every answer so the hot query (mastery by
standard) needs no join, and so each Pub/Sub/BigQuery event is self-contained. A composite foreign key makes the
database reject any copy that disagrees with its question. That's the trade-off conversation: performance and
analytics shape versus duplication risk, with the risk engineered away.

**4. "Liveness must not depend on the database."** If it did, a database outage would make Kubernetes restart every
pod in a loop. The database check goes in readiness, which takes the pod out of the load balancer without killing
it. A small decision with a big blast-radius difference.

**5. "Schema changes without downtime."** Adding a required `login_code` to a table full of students used **expand →
backfill → contract**. `alembic check` proves the models match the migrations, and tests build their database from
the migrations, so a bad migration fails CI, not production.

**6. A debugging story (STAR).**
- *Situation:* intermittent 502s from nginx even though the API was up.
- *Task:* find out why recovery after an API restart took up to 10 seconds.
- *Action:* nginx's error log said `no live upstreams`. `getent` showed `host.docker.internal` resolving to IPv4
  *and* an unreachable IPv6 address, so nginx treated it as two servers and marked both down for its default
  10 seconds. The first fix (`extra_hosts`) didn't help, and I said so instead of shipping it.
- *Result:* an explicit upstream with `max_fails=0`, tested off → on → immediate 200.
- *Lesson:* read the proxy's own logs first, and test recovery, not just the happy path.

**7. "Polling now, Pub/Sub later, and I can explain when to switch."** Polling every 15 seconds is simple and
correct at classroom scale. Pub/Sub plus Server-Sent Events gives about 1-second updates and no idle traffic, but
brings long-lived connections, per-pod subscriptions, and timeouts to raise at three layers (nginx, the load
balancer's `BackendConfig`, the app). That's the kind of right-sizing conversation a customer engineer has with a
customer.

**8. "Designed for the actual users."** 10-year-olds on shared Chromebooks: 7:1 contrast, 64px tap targets,
keyboard-only use, no timers, tokens erased when the tab closes. Teachers: a heatmap where level is shown by symbol
+ percent + color, with a colorblind-checked palette, so "who needs help on which standard" is visible in seconds.
Student codes are hidden by default because dashboards end up on projectors.

**9. "Tests against the real database."** 87 tests run against real Postgres, each inside a rolled-back transaction,
so they're isolated and fast (under a second). They cover the security edges: forged, expired, edited and unsigned
tokens, and wrong-role access to every endpoint.

**10. "Cost-aware from day one."** Phase 2 costs $0: everything runs on the laptop. The cloud plan uses the
smallest viable tiers (Autopilot bills per pod request; the smallest Cloud SQL tier), and the habit of tearing down
idle infrastructure.

---

## 15. Lessons learned

| What happened | Cause | Lesson |
|---|---|---|
| Folders named `#`, `must`, `say` committed into `app/api` | `python3.12 -m venv .venv  # must say ...` in zsh: `#` isn't a comment, so each word became another venv | Keep `# comments` out of commands; check `git status` before committing |
| `.env` got `# change this…` appended to a value | Same zsh issue, with `echo ... >> .env  # comment` | Same |
| An empty file `app/api/0002` appeared | `alembic upgrade head  # ... 0001 -> 0002`: the `>` became a **redirect** into a file named `0002` | Same; and `>` overwrites, `>>` appends |
| Every test errored with "Can't reach Postgres" | The db container had been stopped | `docker compose ps`, then `up -d db` |
| `localhost:8000/docs` wouldn't load | uvicorn wasn't running; tests don't start a server | The API runs in its own terminal tab |
| A test server failed with "address in use" | Another uvicorn was already on port 8000 | `lsof -nP -iTCP:8000 -sTCP:LISTEN` shows who has the port |
| An edited passcode wasn't accepted | `--reload` watches code, not `.env` | Restart uvicorn after editing `.env` |
| Old JavaScript after an edit | Browser cache | Hard refresh: Cmd+Shift+R |
| 502s right after the API restarted | nginx marked the upstream down (see §12) | `max_fails=0`; test recovery |
| VS Code said packages were "not installed" | It was using a different Python | Select `.venv/bin/python` as the interpreter |

---

## ACE exam notes

**Containers and GKE (Phase 3 preview)**
- **Liveness vs readiness.** A liveness failure **restarts** the container. A readiness failure **removes the pod
  from the Service's endpoints**, so it gets no traffic, but the pod keeps running. A **startup probe** gives
  slow-starting apps time before liveness checks begin. Don't make liveness depend on the database.
- **Configuration:** environment variables come from a **ConfigMap** (non-secret) or a **Secret**. On GCP, prefer
  **Secret Manager**, read with **Workload Identity**: no service account key files.
- **Images** are built for `linux/amd64` (an M-series Mac builds `arm64` by default), pushed to **Artifact
  Registry**, and referenced by tag or, better, by digest.
- **Services:** inside the cluster, pods reach each other by **Service name** (`http://api:8000`). That's what
  `API_HOST` becomes.

**Cloud SQL (Phase 4 preview)**
- Connect with the **Cloud SQL Auth Proxy** (or the language connector) using IAM, ideally over **private IP**. Don't
  open the instance to `0.0.0.0/0`.
- **Migrations** (`alembic upgrade head`) run as a step before or during deployment, for example a Kubernetes Job or
  a Cloud Build step, not by hand on production.
- **The smallest tier** for practice; **stop or delete** the instance when idle. Cloud SQL bills while running.

**Logging**
- Containers on GKE log to stdout/stderr, and Cloud Logging collects it automatically.
- **Structured (JSON) logs** with a `severity` field get proper log levels and searchable fields in Logs Explorer.

**Security and identity**
- **IAP (Identity-Aware Proxy)** checks a user's Google identity at the **HTTPS load balancer** before requests reach
  the app. It needs an HTTPS load balancer, and users need `roles/iap.httpsResourceAccessor`.
- **Cloud Armor** attaches to the external HTTPS load balancer: WAF rules, IP allow/deny lists, and **rate limiting**
  (throttle or ban by client IP), for example on a sign-in endpoint.
- **Least privilege also applies inside the app:** the student and teacher roles, enforced on every endpoint.

**Pub/Sub → real-time dashboards (Phase 6 preview)**
- **Topic → subscription → subscriber.** Each **subscription** gets its own copy of every message. Within one
  subscription, each message goes to **one** subscriber. So several API pods that each need every event need
  **separate subscriptions**.
- **Pull vs push subscriptions:** with *pull*, the subscriber asks for messages; with *push*, Pub/Sub POSTs each
  message to an HTTPS endpoint.
- **BigQuery subscriptions** write messages straight into a BigQuery table, with no code in between. Looker Studio
  then reads BigQuery.
- **Browsers can't subscribe to Pub/Sub directly** (it needs Google Cloud credentials). For live dashboards, the API
  relays events to browsers with **Server-Sent Events** (`EventSource`) or WebSockets.
  - The page's `refresh()` stays the same; it's just *triggered* by events, with a slow fallback timer.
  - nginx then needs `proxy_buffering off` and a long `proxy_read_timeout` for that route.
  - The GKE load balancer's backend timeout (30 s by default, set with a `BackendConfig`) must be raised for
    long-lived connections.
- **Polling vs push:** polling is simpler and fine at small scale, but costs requests even when nothing changes.
  Push gives about 1-second freshness and no idle traffic, at the price of more moving parts.

**Cost reminders**
- Everything in Phase 2 runs locally and **costs nothing**.
- From Phase 3 on: GKE Autopilot bills per pod resource request, and Cloud SQL bills while the instance runs. Tear
  down or stop resources when you're done for the day.
