"""Look up seeded ids and build submissions, using the same keys as scripts/seed_data.py."""

import uuid

from app.auth import create_student_token, create_teacher_token, new_login_code
from app.models import Classroom, Student
from app.schemas import LETTERS
from seed import stable_id
from seed_data import CLASSROOM_NAME, EXIT_TICKETS, QUESTIONS, student_display_names

QUESTIONS_BY_KEY = {q["key"]: q for q in QUESTIONS}
TICKETS_BY_KEY = {t["key"]: t for t in EXIT_TICKETS}
STUDENT_NAMES = student_display_names()
CLASSROOM_ID = stable_id("classroom", CLASSROOM_NAME)


def ticket_id(key: str) -> str:
    return str(stable_id("ticket", key))


def question_id(key: str) -> str:
    return str(stable_id("question", key))


def student_id(index: int) -> str:
    return str(stable_id("student", f"{CLASSROOM_NAME}:{STUDENT_NAMES[index]}"))


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def teacher_headers() -> dict:
    return bearer(create_teacher_token().token)


def student_headers(student: int | str) -> dict:
    """Signed in as a seeded student (by index) or any student (by id)."""
    sid = student_id(student) if isinstance(student, int) else student
    return bearer(create_student_token(uuid.UUID(sid)).token)


def add_other_classroom(db_session) -> tuple[str, str]:
    """A second classroom with one student, whose data must never show up in Class 5-201.
    Returns (classroom id, student id)."""
    classroom = Classroom(id=uuid.uuid4(), name="Class 5-999")
    student = Student(
        id=uuid.uuid4(), classroom_id=classroom.id, display_name="Test Z.", login_code=new_login_code()
    )
    db_session.add(classroom)
    db_session.flush()
    db_session.add(student)
    db_session.flush()
    return str(classroom.id), str(student.id)


def wrong_letter(correct: str) -> str:
    return LETTERS[(LETTERS.index(correct) + 1) % 4]


def answers_for(ticket_key: str, wrong: tuple[str, ...] = ()) -> list[dict]:
    """Answers for every question on a ticket: correct, except question keys in `wrong`."""
    answers = []
    for key in TICKETS_BY_KEY[ticket_key]["questions"]:
        correct = QUESTIONS_BY_KEY[key]["correct"]
        chosen = wrong_letter(correct) if key in wrong else correct
        answers.append({"question_id": question_id(key), "chosen_choice": chosen})
    return answers


def submit(client, ticket_key: str, student: int | str, wrong: tuple[str, ...] = ()):
    """Submit a whole ticket as a student (by index or id). Returns the response."""
    return client.post(
        f"/exit-tickets/{ticket_id(ticket_key)}/submissions",
        json={"answers": answers_for(ticket_key, wrong=wrong)},
        headers=student_headers(student),
    )
