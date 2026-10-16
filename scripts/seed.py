"""Load synthetic demo data into the database. Safe to run more than once.

Run from the repo root with the API's virtual environment active:
    python scripts/seed.py

How re-running stays safe:
- Every row's id is calculated from a stable key (uuid5), so the same question
  always gets the same id.
- Every insert is an upsert (INSERT ... ON CONFLICT DO UPDATE): if the row already
  exists, it's updated in place instead of duplicated.
- Everything runs in one transaction: either all of it is saved, or none of it.
"""

import logging
import sys
import uuid
from pathlib import Path

# Make the API's "app" package importable (it lives in app/api).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "api"))

from sqlalchemy import Connection, Table  # noqa: E402
from sqlalchemy.dialects.postgresql import insert  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.logging_config import configure_logging  # noqa: E402
from app.models import (  # noqa: E402
    Classroom,
    ExitTicket,
    ExitTicketQuestion,
    Question,
    Standard,
    Student,
)
from seed_data import (  # noqa: E402
    CLASSROOM_NAME,
    EXIT_TICKETS,
    QUESTIONS,
    STANDARDS,
    student_display_names,
)

logger = logging.getLogger("seed")

SEED_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "exit-ticket/seed")


def stable_id(kind: str, key: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"{kind}:{key}")


def validate() -> None:
    """Catch mistakes in seed_data.py before touching the database."""
    subjects = {s["code"]: s["subject"] for s in STANDARDS}
    questions = {q["key"]: q for q in QUESTIONS}
    assert len(questions) == len(QUESTIONS), "duplicate question key"
    for q in QUESTIONS:
        assert q["standard"] in subjects, f"{q['key']}: unknown standard {q['standard']}"
        assert len(q["choices"]) == 4, f"{q['key']}: needs exactly 4 choices"
        assert len(set(q["choices"])) == 4, f"{q['key']}: choices must be different"
        assert q["correct"] in "ABCD", f"{q['key']}: correct must be A-D"
    for t in EXIT_TICKETS:
        assert 3 <= len(t["questions"]) <= 5, f"{t['key']}: needs 3-5 questions"
        for key in t["questions"]:
            assert key in questions, f"{t['key']}: unknown question {key}"
            standard = questions[key]["standard"]
            assert subjects[standard] == t["subject"], f"{t['key']}: {key} is the wrong subject"


def upsert(connection: Connection, table: Table, rows: list[dict], key_columns: list[str]) -> None:
    statement = insert(table).values(rows)
    # Only overwrite columns the seed provides, so e.g. created_at keeps its first value.
    updates = {name: statement.excluded[name] for name in rows[0] if name not in key_columns}
    connection.execute(statement.on_conflict_do_update(index_elements=key_columns, set_=updates))


def seed(connection: Connection) -> None:
    upsert(connection, Standard.__table__, STANDARDS, ["code"])

    upsert(
        connection,
        Question.__table__,
        [
            {
                "id": stable_id("question", q["key"]),
                "standard_code": q["standard"],
                "prompt": q["prompt"],
                "choices": q["choices"],
                "correct_choice": q["correct"],
                "explanation": q["explanation"],
            }
            for q in QUESTIONS
        ],
        ["id"],
    )

    upsert(
        connection,
        ExitTicket.__table__,
        [
            {"id": stable_id("ticket", t["key"]), "title": t["title"], "subject": t["subject"]}
            for t in EXIT_TICKETS
        ],
        ["id"],
    )

    # Note: re-ordering questions within an existing ticket can clash with the
    # (ticket_id, position) unique rule; adding questions or editing text is fine.
    upsert(
        connection,
        ExitTicketQuestion.__table__,
        [
            {
                "ticket_id": stable_id("ticket", t["key"]),
                "question_id": stable_id("question", key),
                "position": position,
            }
            for t in EXIT_TICKETS
            for position, key in enumerate(t["questions"], start=1)
        ],
        ["ticket_id", "question_id"],
    )

    classroom_id = stable_id("classroom", CLASSROOM_NAME)
    upsert(connection, Classroom.__table__, [{"id": classroom_id, "name": CLASSROOM_NAME}], ["id"])

    upsert(
        connection,
        Student.__table__,
        [
            {
                "id": stable_id("student", f"{CLASSROOM_NAME}:{name}"),
                "classroom_id": classroom_id,
                "display_name": name,
            }
            for name in student_display_names()
        ],
        ["id"],
    )


def main() -> None:
    configure_logging(get_settings().log_level)
    validate()
    with get_engine().begin() as connection:  # begin() = one transaction, committed at the end
        seed(connection)
    logger.info(
        "Seeded %d standards, %d questions, %d exit tickets, 1 classroom, %d students",
        len(STANDARDS),
        len(QUESTIONS),
        len(EXIT_TICKETS),
        len(student_display_names()),
    )


if __name__ == "__main__":
    main()
