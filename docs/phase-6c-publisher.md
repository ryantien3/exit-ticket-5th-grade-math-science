# Phase 6C — The app publishes answer events

Phase 6C fills in `publish_answer_event()`, the function [Phase 2](phase-2-local-app.md) left as a log line. With
`PUBSUB_TOPIC` set, every graded answer is now published to `answer-events` (built in [6B](phase-6b-analytics.md)), and
the BigQuery subscription writes it to `exit_ticket_analytics.answer_events`. With `PUBSUB_TOPIC` unset (local dev,
tests), nothing leaves the laptop.

The code follows the [6A design](phase-6a-design.md) §3.7 and §4. Results:

- **108 tests pass**: the 91 from before plus 17 new ones.
- **A real local submission** reached BigQuery in 380 ms.
- **No image was built**; that's 6D.

| Part | What | PR |
|---|---|---|
| 6A | Design, contract, Avro schema | #28 |
| 6B | `analytics` Terraform layer | #29 |
| **6C** | Publisher in the API, contract v2, tests, local end-to-end run | (this PR) |
| 6D | On GKE as `exit-ticket-api`; drills; test-row cleanup | |
| 6E | Looker Studio | |

> **About the commands in this doc.** Code blocks contain commands only, no `#` comments: in zsh, `#` is not a
> comment unless `setopt interactivecomments` is on. No project ID is committed: it comes from
> `$(gcloud config get project)`.

**Contents**

1. [Concepts](#1-concepts)
2. [The code](#2-the-code)
3. [Failure modes](#3-failure-modes)
4. [The outbox gap](#4-the-outbox-gap)
5. [Tests](#5-tests)
6. [Local run against the real topic](#6-local-run-against-the-real-topic)
7. [Known gaps](#7-known-gaps)
8. [Lessons learned](#8-lessons-learned)
9. [Interview talking points](#9-interview-talking-points)
10. [ACE exam notes](#ace-exam-notes)

---

## 1. Concepts

### Application Default Credentials (ADC)

The app never handles a credential. `pubsub_v1.PublisherClient()` is created with **no arguments**, and the Google
auth library looks for credentials itself, in a fixed order
([ADC docs](https://cloud.google.com/docs/authentication/application-default-credentials)):

1. A file named by `GOOGLE_APPLICATION_CREDENTIALS`. We never set it: it would point at a key file, and the project has
   none (CLAUDE.md rule 3).
2. The file `gcloud auth application-default login` writes on the Mac. That's **your user** when you run locally.
3. The metadata server. On GKE with Workload Identity, that's **`exit-ticket-api`**. Google calls this "the preferred
   method for finding credentials in a production environment".

The same code therefore runs as a different identity in each place, and nothing in the code changes.

### Why your user could publish locally, and why that proves less than it seems

You're project **Owner**, and Owner includes `pubsub.topics.publish` on every topic. So the local run (§6) proves:

- the code works,
- the message matches the schema,
- the pipeline reaches BigQuery.

It does **not** prove that `exit-ticket-api` may publish. That account holds `roles/pubsub.publisher` on
`answer-events` **only**, a much narrower grant. **6D proves it**, from the pod.

There is a middle step: your user holds `iam.serviceAccountTokenCreator` on `exit-ticket-api` (a Phase 4 grant). So
`gcloud auth application-default login --impersonate-service-account=exit-ticket-api@PROJECT_ID.iam.gserviceaccount.com`
would make local ADC act as the api. It wasn't used here, because it replaces your local ADC file until you log in
again.

### Futures, batching and a background thread

`publish()` doesn't send anything itself. It adds the message to a batch and returns a **future**, a promise of the
result. The library's background thread sends a batch when any of these is reached
([BatchSettings](https://cloud.google.com/python/docs/reference/pubsub/latest/google.cloud.pubsub_v1.types.BatchSettings)):

- 100 messages,
- 1 MB,
- 10 ms (the default `max_latency`).

When the server answers, the future resolves to a message ID or an exception, and any **done-callback** attached to it
runs. In this library the future is a standard `concurrent.futures.Future` (checked: `issubclass` is `True`), so
`concurrent.futures.wait` works on it.

That's what makes publishing **non-blocking**. The local log shows it:

| Time | What happened |
|---|---|
| 01:49:48.145 | `POST …/submissions 201`: the response is already on its way to the browser |
| 01:49:48.505 | `answer_event_published` ×4, logged by the callbacks |

The request never waited for Pub/Sub.

### One client per process

A `PublisherClient` holds a gRPC connection, a batch and a thread. Creating one per request would be slow, and it would
throw away batching. So there's one per process:

- **Created in FastAPI's `lifespan`**, which runs in the worker process. Any fork by uvicorn's reloader or by workers
  has already happened by then, so the gRPC connection isn't shared across a fork.
- **Stopped on shutdown.** `stop()` "immediately publish[es] all outstanding messages" and makes later `publish()` calls
  raise `RuntimeError`
  ([Client](https://cloud.google.com/python/docs/reference/pubsub/latest/google.cloud.pubsub_v1.publisher.client.Client)).
  It doesn't block, so `stop_publisher()` then waits up to 10 seconds for the futures still in flight.

---

## 2. The code

| File | What |
|---|---|
| [requirements.txt](../app/api/requirements.txt) | `google-cloud-pubsub==2.43.0` |
| [requirements-dev.txt](../app/api/requirements-dev.txt) | `fastavro==1.13.1` (tests only) |
| [config.py](../app/api/app/config.py) | `pubsub_topic: str \| None`. A full path is required (`projects/…/topics/…`), so the short name `answer-events` fails at startup. Empty counts as unset |
| [events.py](../app/api/app/events.py) | `AnswerEvent` (the contract), `to_message()`, `start_publisher()`, `stop_publisher()`, `publish_answer_event()`, and the callback |
| [main.py](../app/api/app/main.py) | `lifespan`: `start_publisher(settings.pubsub_topic)` before `yield`, `stop_publisher()` after |
| [routes/exit_tickets.py](../app/api/app/routes/exit_tickets.py) | One query for the ticket's standards; `standard_domain` and `subject` on each event |
| [conftest.py](../app/api/tests/conftest.py) | `PUBSUB_TOPIC=""` forced, so no test can reach the real topic even if `.env` sets one |
| [test_events.py](../app/api/tests/test_events.py), [test_config.py](../app/api/tests/test_config.py) | 17 new tests (§5) |
| [app/api/README.md](../app/api/README.md) | `PUBSUB_TOPIC` added to the settings table |

### Choosing the version

The release notes came from the package's CHANGELOG on GitHub. Google's changelog page stops at 2.39.0, because
releases since then come from the `google-cloud-python` repository.

| Version | Date | Note |
|---|---|---|
| 2.44.0 | 2026-11-12 | "update API sources and regenerate". Only 6 days old at the time, so skipped |
| **2.43.0** | 2026-10-15 | Same kind of change, a month in use. **Pinned** |
| 2.42.0 | 2026-10-01 | Declares Python 3.15 support |
| 2.39.1 | 2026-08-06 | "bump grpcio to 1.59.0; require Python 3.10+": fine on our 3.12 |

Nothing breaking has changed since 2.40. The image is unchanged in this PR. The next build (6D) installs the new pin,
still for `linux/amd64`.

### The contract: `AnswerEvent`

- **`extra="forbid"`.** An unknown field is an error. That matters because the **Pub/Sub schema ignores unknown keys**
  (6A §6 test 4). The model is therefore what keeps `display_name` and `login_code` out.
- **`frozen=True`.** An event can't be changed after it's built.
- **Fixed values are `Literal` types**, so nothing else is accepted:
  - `event_type = "answer_submitted"`
  - `schema_version = 2`
  - `subject` is `math` or `science`
  - `chosen_choice` is `A` to `D`
- **`event_id` is derived from the answer** (since 6D): `answer_event_id(answer_id)` =
  `uuid5(EVENT_NAMESPACE, "answer_submitted:" + answer_id)`, with a fixed namespace constant. The app, a retry and a
  republish from Cloud SQL all get the same ID, so the view keeps one row per answer. An explicit `event_id` that
  doesn't match is rejected. **As first shipped in 6C it was `uuid4()`**, which covered retries of the same object but
  not a rebuilt event (see §4). The original wording: it was made once per event, and every retry of the same message carries the same
  value. That's the dedup key the view uses (6A §3.8).
- **`answered_at` must be timezone-aware.** A naive time would be read as the wrong instant.
- **`to_message()`** produces the Avro JSON encoding. UUIDs become strings, and `answered_at` becomes integer
  microseconds: `(dt - EPOCH) // timedelta(microseconds=1)`. That's integer division, so no digits are lost to float
  rounding.

### The call site: already after the commit

The publish loop was already after `session.commit()` (Phase 2 put it there), so **nothing moved**. What the diff adds:

```python
standards = {s.code: s for s in session.scalars(select(Standard).where(Standard.code.in_({row.standard_code for row in rows})))}
...
            standard_domain=standards[row.standard_code].domain,
            subject=standards[row.standard_code].subject,
```

The order inside `submit_exit_ticket`:

1. Validate, grade, build `rows`.
2. **Build** the events from the same values (ORM objects expire after commit).
3. `session.commit()`. On a race (`IntegrityError`) it returns 409, and **nothing is published**.
4. **Publish**, one call per answer, each in `try/except`.
5. Return the score.

A test proves the order through the real endpoint (§5).

### Logging: what's in each line

| Event | Log line | Fields |
|---|---|---|
| Startup, publishing off | `PUBSUB_TOPIC is not set: answer events will be logged, not published` | — |
| Startup, publishing on | `Publishing answer events to projects/…/topics/answer-events` | — |
| Publishing off, per event | `answer_event` | the full event (as before) |
| Published | `answer_event_published` | `event_id`, `message_id` |
| Failed | `answer_event_publish_failed` (ERROR) | `event_id`, `error_type`, `error` (first 500 characters) |
| Shutdown with messages unconfirmed | `answer_events_unconfirmed_at_shutdown` (WARNING) | `count` |

**One change from 6A.** The design said the full `answer_event` log line would stay "in both cases". Now it's logged
**only when publishing is off**. When publishing is on, the logs carry just IDs. Otherwise Cloud Logging would hold a
second copy of every student's answers, and that copy wouldn't be covered by the BigQuery access controls.

---

## 3. Failure modes

| What fails | When you find out | What the student sees | What's logged | Where the answer is |
|---|---|---|---|---|
| `PUBSUB_TOPIC` malformed | Startup: the app refuses to start (validation error naming `pubsub_topic`) | Nothing (no pod becomes ready) | The validation error | — |
| Message doesn't match the schema (a code bug) | Callback, about 0.1–0.5 s after the response | Their score, normally | `answer_event_publish_failed`, `INVALID_ARGUMENT` | Cloud SQL |
| No permission (e.g. a missing grant on GKE) | Callback | Their score | `…publish_failed`, `PermissionDenied` | Cloud SQL |
| Pub/Sub slow or unreachable | Callback, after the library's retries give up | Their score | `…publish_failed`, timeout or `ServiceUnavailable` | Cloud SQL |
| `publish()` itself raises (after `stop()`, message too large) | Immediately, caught inside `publish_answer_event()` | Their score | `…publish_failed`, e.g. `RuntimeError` | Cloud SQL |
| Anything else unexpected in `publish_answer_event()` | Caught by the call site's `try/except` | Their score | `Failed to publish answer event …` with the traceback | Cloud SQL |
| Pod gets SIGTERM with messages batched | Shutdown: `stop()` flushes and waits up to 10 s | — | Count of unconfirmed, if any | Cloud SQL |
| Pod killed hard (OOM, `kill -9`) between commit and send | Never: nothing runs | Their score | Nothing | Cloud SQL only: **the gap** (§4) |
| Subscription broken (table gone, grant lost) | Not in the app: Pub/Sub accepted the message | Their score | `answer_event_published` (correctly) | Pub/Sub backlog, 7 days (6A §3.5) |
| Duplicates (publisher retry or redelivery) | Never an error | — | Two `message_id`s possible | BigQuery; the view keeps one per `event_id` |

The rule throughout: **the student's submission never fails because of analytics.** Cloud SQL is the source of truth,
and BigQuery is a copy that can be repaired.

---

## 4. The outbox gap

The answers are committed to Cloud SQL, and then, separately, the events are handed to Pub/Sub. Those are **two
systems with no shared transaction**. If the process dies in between (out of memory, node preemption, `kill -9`), the
answers are saved and the events are gone. Nothing logs it, because nothing runs.

**The standard fix is the transactional outbox:**

1. In the **same transaction** as the answers, insert one row per event into an `outbox` table. Either both are saved
   or neither is.
2. A **relay** (a loop in the app, or a separate job) reads unsent outbox rows, publishes them, and marks them sent.
3. If the relay crashes after publishing but before marking a row, the row is published again. That's fine, because the
   `event_id` makes it a duplicate the view removes.

**Why it's deferred:**

- It needs a relay process, an outbox table and a migration.
- The window here is milliseconds per submission. The local run went from commit to published in about 0.36 s.
- Nothing is truly lost: every answer is in Cloud SQL, and `scripts/republish-answers.py` (6D) can republish any
  missing `answer_id`s. **What makes that safe to run twice is the deterministic `event_id`**: a republished answer
  carries the same `event_id` as the original, so the view keeps one copy. With the random `uuid4()` this PR first
  shipped, it was **not** safe: each run would have added new IDs, and the view would have counted those answers
  again. Rows written before the fix have random IDs, so the script also skips `answer_id`s already in BigQuery.

**When to revisit:** if analytics ever becomes something people act on in real time, or if the logs show
`answer_events_unconfirmed_at_shutdown` in practice.

---

## 5. Tests

`pytest -q`: **108 passed** (91 before + 17). No network: `FakePublisherClient` replaces the real client and returns
real `concurrent.futures.Future` objects, so the callbacks run exactly as in production.

| Requirement | Test(s) |
|---|---|
| Fields exactly as the contract | `test_model_fields_match_the_avro_schema_exactly`: model fields == `.avsc` fields, same names, same order |
| Payload matches the `.avsc` | `test_message_decodes_with_the_avro_schema`: **fastavro** decodes the message with the real schema file; `answered_at == 1795010730123456`; no extra or missing keys. Also run on each of the 5 messages of a real submission |
| `event_id` | Since 6D (111 tests): `test_same_answer_gets_the_same_event_id_in_every_build`, `test_different_answers_get_different_event_ids`, `test_event_id_namespace_never_changes` (pins one known value), `test_an_explicit_event_id_must_be_the_derived_one`. The endpoint test checks each published `event_id` == `answer_event_id(saved row id)`. (6C's original test checked only that two builds got *different* IDs, which was the flaw) |
| No name or sign-in code | `test_a_name_or_sign_in_code_cannot_be_added` (the model refuses them); `test_payload_has_no_name_or_sign_in_code` (the real student's `display_name` and `login_code` aren't in any published byte) |
| Naive timestamps | `test_naive_timestamp_is_rejected` |
| No-op path | `test_no_topic_creates_no_client_and_logs_once[None]` and `[""]`: no client, one info line, the event still logged |
| Success | `test_publish_sends_the_message_and_logs_the_message_id` |
| Publish errors don't fail anything | `test_failed_publish_is_logged_without_student_data` (and no student/answer/class/question ID in the log text); `test_publish_that_raises_immediately_is_logged_not_raised`; the existing `test_publish_failure_does_not_fail_submission` (201 even when the publisher raises) |
| Once per answer, after the commit | `test_submission_publishes_one_event_per_answer_after_the_commit`: the recorded order is `commit, publish ×5`; answer IDs == saved rows; unique `event_id`s; correct domain and subject |
| Nothing published when rejected | `test_rejected_submission_publishes_nothing` (duplicate → 409 → still 5 messages) |
| Shutdown | `test_stop_flushes_and_waits_for_messages_in_flight` |
| Config | `test_pubsub_topic_is_optional`, `test_pubsub_topic_must_be_a_full_path` |

A problem found while writing the tests: the first topic pattern used `\w{2,254}`, and pydantic's regex engine refused
it ("Compiled regex exceeds size limit"), because `\w` matches every Unicode letter. Spelling out the ASCII characters
fixed it.

---

## 6. Local run against the real topic

Dev mode: Postgres and nginx in Docker, uvicorn on the Mac, with **your user's ADC**.

**Tab 1** (left running):
```bash
export CLOUDSDK_PYTHON=/opt/homebrew/bin/python3.13
cd ~/projects/exit-ticket-5th-grade-math-science
docker compose up -d --wait db web
cd app/api
source .venv/bin/activate
pip install -r requirements-dev.txt
alembic upgrade head
python ../../scripts/seed.py
gcloud auth application-default print-access-token > /dev/null && echo "ADC OK"
PUBSUB_TOPIC="projects/$(gcloud config get project)/topics/answer-events" uvicorn app.main:app --reload
```

**Why on the command line:** `PUBSUB_TOPIC=… uvicorn …` sets it for that one process only. In `.env` it would quietly
turn publishing on in every later session.

**Tab 2:**
```bash
cd ~/projects/exit-ticket-5th-grade-math-science
grep '^TEACHER_PASSCODE=' app/api/.env | cut -d= -f2- | tr -d '\n' | pbcopy
```

Then, in the browser:

1. Sign in at http://localhost:8080/teacher.html, then clear the clipboard with `pbcopy < /dev/null`.
2. Release a ticket, and note a student's code from **Show student codes**.
3. In a **private window**, sign in at http://localhost:8080/ with that code, answer and submit.

Then:

```bash
PROJECT="$(gcloud config get project)"
bq --project_id="$PROJECT" --dataset_id=exit_ticket_analytics query --use_legacy_sql=false 'SELECT event_id, message_id, standard_code, standard_domain, subject, chosen_choice, is_correct, answered_at, TIMESTAMP_DIFF(publish_time, answered_at, MILLISECOND) AS latency_ms, JSON_VALUE(attributes, "$.googclient_schemarevisionid") AS schema_revision FROM answer_events WHERE event_type = "answer_submitted" AND answered_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR) ORDER BY message_id'
bq --project_id="$PROJECT" --dataset_id=exit_ticket_analytics query --use_legacy_sql=false 'SELECT COUNT(*) AS app_events_in_view FROM answer_events_clean WHERE event_type = "answer_submitted" AND answered_at > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 HOUR)'
gcloud pubsub subscriptions pull answer-events-dlq-inspect --limit=5
```

The `answered_at` filter lets BigQuery read only today's partition.

### Results (2026-11-19)

| Check | Result | OK? |
|---|---|---|
| Startup | `Publishing answer events to projects/PROJECT_ID/topics/answer-events`, then `Application startup complete` | ✅ |
| Submission | "Decimal Operations" (4 questions): `POST …/submissions 201` at 01:49:48.145 | ✅ |
| Callbacks | 4 × `answer_event_published` at 01:49:48.505, message IDs `…2002`–`…2005`: **after** the response | ✅ |
| Cloud SQL | 4 answers saved for that ticket | ✅ |
| BigQuery | 4 rows, same message IDs; `NY-5.NBT.7`, `NY-5.NBT`, `math`; one `is_correct = false`; `latency_ms = 380`; revision `cb8202da` | ✅ |
| View | `app_events_in_view = 4` | ✅ |
| DLQ | `Listed 0 items.` | ✅ |

**What the 380 ms covers:** from grading (`answered_at`, just before the commit) to Pub/Sub accepting the message. That
includes the commit, the 10 ms batch window, and a round trip from the Mac to Google. From GKE in us-east4 it should be
lower. 6D will show.

These 4 rows are real-looking `answer_submitted` events from a synthetic student. They stay; they're valid data.

---

## 7. Known gaps

- **Not on GKE yet (6D).** `PUBSUB_TOPIC` goes into [k8s/configmap.yaml](../k8s/configmap.yaml) and the explicit `env:`
  list in [k8s/api.yaml](../k8s/api.yaml). A new image (`linux/amd64`) gets pushed. Then publishing is proven as
  `exit-ticket-api`.
- **The outbox** (§4).
- **No lock file.** `requirements.txt` pins only direct dependencies. google-cloud-pubsub brings in about 10 packages
  (grpcio, protobuf, google-auth, opentelemetry, …) that aren't pinned, so two image builds can differ. A fully pinned
  file with hashes is a small follow-up of its own.
- **Unknown keys at the BigQuery subscription** (6A §7): still a 6D check. The model can't send one now anyway.
- **The `smoke_test` row from 6B** is still in the table, to be deleted at the end of 6D.

---

## 8. Lessons learned

| Lesson | Where |
|---|---|
| **Credentials belong to the environment, not the code.** The same `PublisherClient()` is you on the Mac and `exit-ticket-api` on GKE | §1 |
| **Test with the identity you'll run as, or know what you didn't prove.** Owner hides permission bugs | §1 |
| **Non-blocking needs a callback.** Without it, failures would be invisible. With it, the access log and the published log show the order | §1, §6 |
| **The schema validates; the model filters.** The Pub/Sub schema accepted an extra key in 6A, so a closed model plus a contract test does that job | §2 |
| **Make the contract a test.** Model fields == schema fields, so drift fails CI instead of the pipeline | §5 |
| **Make tests unable to touch production.** `PUBSUB_TOPIC=""` is forced in conftest | §2 |
| **Read the release notes where they actually are.** Google's page stopped at 2.39; the monorepo CHANGELOG had 2.44 | §2 |
| **Count before assuming a bug.** 4 events for a 4-question ticket, checked against Cloud SQL | §6 |

---

## 9. Interview talking points

- *"How do you publish events without slowing the request?"* `publish()` returns a future and the client batches on a
  background thread. The request never waits, and a done-callback logs the message ID or the error. Locally the
  response went out about 0.36 s before the publish confirmations.
- *"What if publishing fails?"* The student still gets their score: Cloud SQL is the source of truth. The failure is
  logged with only the `event_id`, so the event can be traced and backfilled without logging student data.
- *"What's the gap in this design?"* A crash between commit and publish loses the event: a dual write. The fix is a
  transactional outbox plus a relay, made safe by an idempotent `event_id`. It's deferred because the window is tiny and
  the data is recoverable.
- *"How do you keep PII out of the pipeline?"* A closed event model (`extra="forbid"`), a contract test tying it to the
  Avro schema, a test that the real student's name and code aren't in the bytes, and logs that carry only IDs when
  publishing is on. I also tested that the Pub/Sub schema alone doesn't filter unknown keys.
- *"How does the app authenticate to Pub/Sub?"* Application Default Credentials: no keys, no code. On GKE that's
  Workload Identity mapping the pod's Kubernetes service account to `exit-ticket-api`, which can publish to one topic.

---

## ACE exam notes

- **ADC order:** `GOOGLE_APPLICATION_CREDENTIALS`, then the `gcloud auth application-default login` file, then the
  metadata server (attached service account / Workload Identity). Prefer the attached service account in production.
- `gcloud auth login` (for gcloud itself) and `gcloud auth application-default login` (for client libraries) are
  **different** credentials.
- **Impersonation:** `--impersonate-service-account` needs `roles/iam.serviceAccountTokenCreator` on the target account.
- `roles/pubsub.publisher` lets a principal publish. On a topic, it's the least privilege for a producer.
- **Publisher clients batch by default** (100 messages, 1 MB, 10 ms in Python). A published message gets a
  server-assigned `messageId`.
