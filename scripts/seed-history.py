"""Write about 4 weeks of SYNTHETIC exit-ticket history into the database (Cloud SQL is the source of truth).

For the 24 seeded synthetic students of Class 5-201 and the 6 seeded exit tickets, it creates:
- a release of each ticket to the class, dated in the past (existing releases are left alone),
- one submission per (student, ticket) for most students, on a school day after the release,
  graded like the app does (chosen_choice vs the question's correct choice).

Correctness is simulated: each student has an ability, each standard a difficulty, and everyone improves
a little each week. Synthetic students only: the script refuses to run unless the classroom's students
are exactly the seed's synthetic names.

Safe to rerun:
- Answer IDs are uuid5 of (student, ticket, question), and rows are inserted with ON CONFLICT DO NOTHING.
- A (student, ticket) pair that already has any answer (e.g. a real submission made in the app) is skipped
  entirely, so the (student_id, ticket_id, question_id) unique rule always holds and a submission is never
  half real, half synthetic.

This only writes the database. To get the answers into BigQuery: scripts/republish-answers.py.

Usage, from the repo root, with the API's virtualenv and a cloud-sql-proxy on 127.0.0.1:15432 (or local
Postgres):
    DATABASE_URL=postgresql+psycopg://USER@127.0.0.1:15432/exitticket app/api/.venv/bin/python scripts/seed-history.py
    ... scripts/seed-history.py --apply
"""

import argparse
import math
import os
import random
import sys
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "app" / "api"))
sys.path.insert(0, str(REPO / "scripts"))

from sqlalchemy import create_engine, func, make_url, select  # noqa: E402
from sqlalchemy.dialects.postgresql import insert  # noqa: E402

from app.models import Answer, ClassroomExitTicket, Student  # noqa: E402
from seed import stable_id  # noqa: E402
from seed_data import CLASSROOM_NAME, EXIT_TICKETS, QUESTIONS, student_display_names  # noqa: E402

HISTORY_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "exit-ticket/seed-history")
NEW_YORK = ZoneInfo("America/New_York")
LETTERS = ["A", "B", "C", "D"]


def school_days(end: date, weeks: int) -> list[date]:
    start = end - timedelta(weeks=weeks)
    return [start + timedelta(days=n) for n in range((end - start).days) if (start + timedelta(days=n)).weekday() < 5]


def plan(weeks: int, end: date, rng: random.Random):
    """Releases and answer rows to write. Pure: no database access."""
    questions = {q["key"]: q for q in QUESTIONS}
    classroom_id = stable_id("classroom", CLASSROOM_NAME)
    students = [(stable_id("student", f"{CLASSROOM_NAME}:{n}"), rng.gauss(0, 0.9)) for n in student_display_names()]
    standards = sorted({q["standard"] for q in QUESTIONS})
    difficulty = {code: rng.uniform(-0.6, 0.9) for code in standards}

    days = school_days(end, weeks)
    step = (len(days) - 3) / max(len(EXIT_TICKETS) - 1, 1)
    release_days = [days[round(i * step)] for i in range(len(EXIT_TICKETS))]

    releases, answers = [], []
    for ticket, released in zip(EXIT_TICKETS, release_days):
        ticket_id = stable_id("ticket", ticket["key"])
        releases.append(
            {
                "classroom_id": classroom_id,
                "ticket_id": ticket_id,
                "released_at": datetime.combine(released, time(8, 30), NEW_YORK).astimezone(timezone.utc),
            }
        )
        week = (released - days[0]).days / 7
        for student_id, ability in students:
            if rng.random() > 0.88:  # some students miss a ticket
                continue
            day = rng.choice([d for d in days if released <= d <= released + timedelta(days=2)])
            local = datetime.combine(day, time(9, 0), NEW_YORK) + timedelta(minutes=rng.randrange(0, 6 * 60))
            answered_at = local.astimezone(timezone.utc).replace(microsecond=rng.randrange(1_000_000))
            for key in ticket["questions"]:
                q = questions[key]
                p = 1 / (1 + math.exp(-(0.9 + ability - difficulty[q["standard"]] + 0.25 * week)))
                is_correct = rng.random() < p
                chosen = q["correct"] if is_correct else rng.choice([c for c in LETTERS if c != q["correct"]])
                answers.append(
                    {
                        "id": uuid.uuid5(HISTORY_NAMESPACE, f"{student_id}:{ticket_id}:{key}"),
                        "student_id": student_id,
                        "ticket_id": ticket_id,
                        "question_id": stable_id("question", key),
                        "standard_code": q["standard"],
                        "chosen_choice": chosen,
                        "is_correct": is_correct,
                        "answered_at": answered_at,
                    }
                )
    return classroom_id, releases, answers


def summarize(answers: list[dict]) -> None:
    by_standard: dict[str, list[bool]] = defaultdict(list)
    for a in answers:
        by_standard[a["standard_code"]].append(a["is_correct"])
    if not answers:
        print("  (nothing new to write)")
        return
    print(
        f"  {len(answers)} answers, {len({(a['student_id'], a['ticket_id']) for a in answers})} submissions, "
        f"{min(a['answered_at'] for a in answers):%Y-%m-%d} to {max(a['answered_at'] for a in answers):%Y-%m-%d} (UTC)"
    )
    for code in sorted(by_standard):
        results = by_standard[code]
        print(f"    {code:12} {len(results):4} answers  {100 * sum(results) / len(results):5.1f}% correct")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write to the database (default: dry run)")
    parser.add_argument("--weeks", type=int, default=4)
    parser.add_argument("--seed", type=int, default=6, help="random seed; same seed = same history")
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL")
    if not url:
        parser.error("set DATABASE_URL (through cloud-sql-proxy on 127.0.0.1, or local Postgres)")
    if make_url(url).host not in ("127.0.0.1", "localhost"):
        parser.error("DATABASE_URL must point at 127.0.0.1/localhost (a local proxy or local Postgres)")

    end = datetime.now(NEW_YORK).date()  # history ends yesterday: nothing today or in the future
    classroom_id, releases, answers = plan(args.weeks, end, random.Random(args.seed))

    engine = create_engine(url)
    with engine.begin() as conn:
        # Synthetic only: the classroom must hold exactly the seed's synthetic students.
        names = set(conn.scalars(select(Student.display_name).where(Student.classroom_id == classroom_id)))
        if names != set(student_display_names()):
            print(f"refusing: classroom students don't match the 24 synthetic seed names ({len(names)} found)")
            return 1

        taken = set(conn.execute(select(Answer.student_id, Answer.ticket_id).distinct()).all())
        new = [a for a in answers if (a["student_id"], a["ticket_id"]) not in taken]
        before = conn.scalar(select(func.count()).select_from(Answer))
        print(f"database: {make_url(url).render_as_string(hide_password=True)}")
        print(f"answers in the database now: {before}")
        print(f"planned: {len(answers)} answers; skipped (student already submitted that ticket): {len(answers) - len(new)}")
        summarize(new)

        if not args.apply:
            print("dry run: nothing written. Add --apply to write.")
            return 0

        conn.execute(insert(ClassroomExitTicket).values(releases).on_conflict_do_nothing())
        inserted = 0
        if new:
            result = conn.execute(insert(Answer).values(new).on_conflict_do_nothing().returning(Answer.id))
            inserted = len(result.all())
        after = conn.scalar(select(func.count()).select_from(Answer))
        print(f"inserted {inserted} answers; answers in the database now: {after}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
