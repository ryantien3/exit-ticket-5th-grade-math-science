"""Publish SYNTHETIC answer events, so the Looker Studio report (Phase 6E) has a month of data.

Every event is a real contract-v2 AnswerEvent (app/events.py), built from the seed data:
- the 24 seeded students of Class 5-201, with the same IDs as in the database,
- the 6 seeded exit tickets with their real question and standard IDs,
- answered_at spread over the last few weeks (school days, 9:00-15:00 New York time).

Correctness is simulated, not real: each student has an ability, each standard a difficulty, and
everyone improves a little each week. There are NO matching rows in Cloud SQL.

Every message carries the Pub/Sub attribute source=synthetic-backfill. It lands in the table's
`attributes` column (write_metadata), so queries can exclude it:
    JSON_VALUE(attributes, "$.source") IS NULL           -- real events only
    JSON_VALUE(attributes, "$.source") = "synthetic-backfill"

IDs are deterministic (uuid5 from student, ticket and question), so running it twice publishes the
same event_ids again and the answer_events_clean view still shows each event once.

Usage (from the repo root, with the API's virtualenv):
    app/api/.venv/bin/python scripts/backfill-answer-events.py                       dry run: summary only
    app/api/.venv/bin/python scripts/backfill-answer-events.py --publish --topic projects/PROJECT_ID/topics/answer-events

Publishing uses Application Default Credentials (on the Mac: your user).
"""

import argparse
import json
import math
import random
import sys
import uuid
from collections import defaultdict
from concurrent import futures
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "app" / "api"))
sys.path.insert(0, str(REPO / "scripts"))

from app.events import AnswerEvent  # noqa: E402
from seed import stable_id  # noqa: E402
from seed_data import CLASSROOM_NAME, EXIT_TICKETS, QUESTIONS, STANDARDS, student_display_names  # noqa: E402

SOURCE = "synthetic-backfill"
BACKFILL_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "exit-ticket/synthetic-backfill")
NEW_YORK = ZoneInfo("America/New_York")
SCHEMA_PATH = REPO / "infra" / "terraform" / "analytics" / "schemas" / "answer_event.avsc"
LETTERS = ["A", "B", "C", "D"]


def school_days(end: date, weeks: int) -> list[date]:
    start = end - timedelta(weeks=weeks)
    days = [start + timedelta(days=n) for n in range((end - start).days)]
    return [day for day in days if day.weekday() < 5]


def build_events(weeks: int, end: date, rng: random.Random) -> list[AnswerEvent]:
    standards = {s["code"]: s for s in STANDARDS}
    questions = {q["key"]: q for q in QUESTIONS}
    classroom_id = stable_id("classroom", CLASSROOM_NAME)
    students = [
        (stable_id("student", f"{CLASSROOM_NAME}:{name}"), rng.gauss(0, 0.9)) for name in student_display_names()
    ]
    difficulty = {code: rng.uniform(-0.6, 0.9) for code in standards}

    days = school_days(end, weeks)
    # Spread the tickets across the period, in seed order, a few days apart.
    release_days = [days[round(i * (len(days) - 3) / max(len(EXIT_TICKETS) - 1, 1))] for i in range(len(EXIT_TICKETS))]

    events = []
    for ticket, released in zip(EXIT_TICKETS, release_days):
        ticket_id = stable_id("ticket", ticket["key"])
        week = (released - days[0]).days / 7
        for student_id, ability in students:
            if rng.random() > 0.88:  # some students miss a ticket
                continue
            later = [d for d in days if released <= d <= released + timedelta(days=2)]
            day = rng.choice(later)
            local = datetime.combine(day, time(9, 0), NEW_YORK) + timedelta(minutes=rng.randrange(0, 6 * 60))
            answered_at = local.astimezone(timezone.utc).replace(microsecond=rng.randrange(1_000_000))
            for key in ticket["questions"]:
                question = questions[key]
                standard = standards[question["standard"]]
                logit = 0.9 + ability - difficulty[standard["code"]] + 0.25 * week
                is_correct = rng.random() < 1 / (1 + math.exp(-logit))
                chosen = question["correct"] if is_correct else rng.choice([c for c in LETTERS if c != question["correct"]])
                key_str = f"{student_id}:{ticket_id}:{key}"
                events.append(
                    AnswerEvent(
                        event_id=uuid.uuid5(BACKFILL_NAMESPACE, f"event:{key_str}"),
                        answered_at=answered_at,
                        answer_id=uuid.uuid5(BACKFILL_NAMESPACE, f"answer:{key_str}"),
                        student_id=student_id,
                        classroom_id=classroom_id,
                        ticket_id=ticket_id,
                        question_id=stable_id("question", key),
                        standard_code=standard["code"],
                        standard_domain=standard["domain"],
                        subject=standard["subject"],
                        chosen_choice=chosen,
                        is_correct=is_correct,
                    )
                )
    return events


def check_against_schema(events: list[AnswerEvent]) -> None:
    """Every message must decode with the real Avro schema (needs fastavro, a dev dependency)."""
    try:
        import io

        import fastavro
    except ImportError:
        print("fastavro not installed: skipping the schema check (pip install -r app/api/requirements-dev.txt)")
        return
    schema = fastavro.parse_schema(json.loads(SCHEMA_PATH.read_text()))
    for event in events:
        list(fastavro.json_reader(io.StringIO(event.to_message().decode()), schema))
    print(f"schema check: all {len(events)} messages decode with answer_event.avsc")


def summarize(events: list[AnswerEvent]) -> None:
    by_standard: dict[str, list[bool]] = defaultdict(list)
    for event in events:
        by_standard[event.standard_code].append(event.is_correct)
    first = min(e.answered_at for e in events)
    last = max(e.answered_at for e in events)
    print(f"{len(events)} events, {len({e.student_id for e in events})} students, "
          f"{len({e.ticket_id for e in events})} tickets, {first:%Y-%m-%d} to {last:%Y-%m-%d} (UTC)")
    for code in sorted(by_standard):
        results = by_standard[code]
        print(f"  {code:12} {len(results):4} answers  {100 * sum(results) / len(results):5.1f}% correct")


def publish(events: list[AnswerEvent], topic: str) -> int:
    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient()
    pending = [publisher.publish(topic, event.to_message(), source=SOURCE) for event in events]
    done, not_done = futures.wait(pending, timeout=120)
    failures = [f for f in done if f.exception() is not None]
    for failure in failures[:3]:
        print(f"  failed: {type(failure.exception()).__name__}: {str(failure.exception())[:200]}")
    published = len(done) - len(failures)
    print(f"published {published}, failed {len(failures)}, unconfirmed {len(not_done)}")
    return 0 if published == len(events) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--publish", action="store_true", help="actually publish (default: dry run)")
    parser.add_argument("--topic", help="full topic path, projects/PROJECT_ID/topics/answer-events")
    parser.add_argument("--weeks", type=int, default=4, help="how far back to spread the events (default 4)")
    parser.add_argument("--seed", type=int, default=6, help="random seed; same seed = same events (default 6)")
    args = parser.parse_args()

    if args.publish and not (args.topic and args.topic.startswith("projects/") and "/topics/" in args.topic):
        parser.error("--publish needs --topic projects/PROJECT_ID/topics/answer-events")

    end = datetime.now(NEW_YORK).date()  # today, not included: nothing in the future
    events = build_events(args.weeks, end, random.Random(args.seed))
    summarize(events)
    check_against_schema(events)
    if not args.publish:
        print("dry run: nothing published. Add --publish --topic ... to send them.")
        return 0
    return publish(events, args.topic)


if __name__ == "__main__":
    sys.exit(main())
