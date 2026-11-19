"""Republish answers from the database (Cloud SQL, the source of truth) to Pub/Sub, idempotently.

For every row in `answers` (joined to its student's classroom and its standard's domain and subject), it
builds the same AnswerEvent the app would (app/events.py), with the DETERMINISTIC event_id
answer_event_id(answer_id). It publishes only the answers whose answer_id isn't in BigQuery yet.

Two layers make it safe to run any number of times:
1. Skip by answer_id: answers already in BigQuery aren't sent again. That matters for rows written before
   event_ids were deterministic (6C to early 6D): they have random event_ids, so the view couldn't
   deduplicate a republished copy of them.
2. Deterministic event_id: if an answer is sent twice anyway (e.g. two runs before BigQuery has caught up),
   both copies have the same event_id and answer_events_clean keeps one.

Each message carries the attribute source=republish, visible in the table's `attributes` column.

Every run ends with a reconciliation of the database against answer_events_clean, by answer_id: the
counts, answers missing from BigQuery, BigQuery rows with no database answer, and answers counted more than
once in the view.

Usage, from the repo root, with the API's virtualenv (incl. requirements-dev.txt) and a cloud-sql-proxy on
127.0.0.1:15432. BigQuery and Pub/Sub use your Application Default Credentials:
    DATABASE_URL=postgresql+psycopg://USER@127.0.0.1:15432/exitticket app/api/.venv/bin/python scripts/republish-answers.py --topic projects/PROJECT_ID/topics/answer-events
    ... --publish
"""

import argparse
import os
import sys
from concurrent import futures
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "app" / "api"))

from sqlalchemy import create_engine, make_url, text  # noqa: E402

from app.events import AnswerEvent  # noqa: E402

SOURCE = "republish"
DATASET = "exit_ticket_analytics"

ANSWERS_SQL = text(
    """
    SELECT a.id AS answer_id, a.answered_at, a.student_id, s.classroom_id, a.ticket_id, a.question_id,
           a.standard_code, st.domain AS standard_domain, st.subject, a.chosen_choice, a.is_correct
    FROM answers a
    JOIN students s ON s.id = a.student_id
    JOIN standards st ON st.code = a.standard_code
    ORDER BY a.answered_at, a.id
    """
)


def read_answers(url: str) -> list[AnswerEvent]:
    with create_engine(url).connect() as conn:
        return [AnswerEvent(**row._asdict()) for row in conn.execute(ANSWERS_SQL)]


def bigquery_answer_counts(project: str) -> dict[str, int]:
    """answer_id -> rows in answer_events_clean (app events only). Normally 1 each."""
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    sql = (
        f"SELECT answer_id, COUNT(*) AS n FROM `{project}.{DATASET}.answer_events_clean` "
        'WHERE event_type = "answer_submitted" GROUP BY answer_id'
    )
    return {row["answer_id"]: row["n"] for row in client.query(sql).result()}


def reconcile(events: list[AnswerEvent], in_view: dict[str, int]) -> bool:
    in_db = {str(e.answer_id) for e in events}
    missing = in_db - in_view.keys()
    extra = in_view.keys() - in_db
    doubled = {a: n for a, n in in_view.items() if n > 1}
    print("reconciliation (by answer_id):")
    print(f"  database answers:              {len(in_db)}")
    print(f"  answers in answer_events_clean: {len(in_view)}  (rows: {sum(in_view.values())})")
    print(f"  missing from BigQuery:         {len(missing)}")
    print(f"  in BigQuery, not in database:  {len(extra)}")
    print(f"  counted more than once:        {len(doubled)}")
    for label, ids in (("missing", sorted(missing)), ("extra", sorted(extra)), ("doubled", sorted(doubled))):
        for answer_id in ids[:5]:
            print(f"    {label}: {answer_id}")
    return not (missing or extra or doubled)


def publish(events: list[AnswerEvent], topic: str) -> int:
    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient()
    pending = [publisher.publish(topic, e.to_message(), source=SOURCE) for e in events]
    done, not_done = futures.wait(pending, timeout=120)
    failed = [f for f in done if f.exception() is not None]
    for f in failed[:3]:
        print(f"  failed: {type(f.exception()).__name__}: {str(f.exception())[:200]}")
    print(f"published {len(done) - len(failed)}, failed {len(failed)}, unconfirmed {len(not_done)}")
    return 0 if not failed and not not_done else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--topic", required=True, help="projects/PROJECT_ID/topics/answer-events")
    parser.add_argument("--publish", action="store_true", help="actually publish (default: dry run, counts only)")
    args = parser.parse_args()

    parts = args.topic.split("/")
    if len(parts) != 4 or parts[0] != "projects" or parts[2] != "topics":
        parser.error("--topic must be projects/PROJECT_ID/topics/answer-events")
    project = parts[1]

    url = os.environ.get("DATABASE_URL")
    if not url or make_url(url).host not in ("127.0.0.1", "localhost"):
        parser.error("set DATABASE_URL to the cloud-sql-proxy on 127.0.0.1 (or local Postgres)")

    events = read_answers(url)
    in_view = bigquery_answer_counts(project)
    to_send = [e for e in events if str(e.answer_id) not in in_view]
    print(f"database answers: {len(events)}; already in BigQuery: {len(events) - len(to_send)}; to publish: {len(to_send)}")

    if args.publish and to_send:
        status = publish(to_send, args.topic)
        print("BigQuery takes a few seconds to show new rows: run again (without --publish) to reconcile.")
        return status
    if args.publish:
        print("nothing to publish")
    else:
        print("dry run: nothing published. Add --publish to send.")
    return 0 if reconcile(events, in_view) else 2


if __name__ == "__main__":
    sys.exit(main())
