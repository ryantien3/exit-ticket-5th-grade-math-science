import re
import uuid

import pytest
from sqlalchemy import select

from app.models import Student
from seed_data import CLASSROOM_NAME
from seed_helpers import (
    CLASSROOM_ID,
    STUDENT_NAMES,
    TICKETS_BY_KEY,
    add_other_classroom,
    student_id,
    submit,
    teacher_headers,
    ticket_id,
)


def get(client, path, **params):
    return client.get(path, params=params, headers=teacher_headers())


def test_list_classrooms(client, db_session):
    other_classroom, _ = add_other_classroom(db_session)
    response = get(client, "/classrooms")
    assert response.status_code == 200
    assert response.json() == [
        {"id": str(CLASSROOM_ID), "name": CLASSROOM_NAME},
        {"id": other_classroom, "name": "Class 5-999"},
    ]


def test_list_students_includes_login_codes(client, db_session):
    add_other_classroom(db_session)
    response = get(client, f"/classrooms/{CLASSROOM_ID}/students")
    assert response.status_code == 200
    students = response.json()
    assert [s["display_name"] for s in students] == sorted(STUDENT_NAMES)
    assert "Test Z." not in {s["display_name"] for s in students}

    saved_code = db_session.scalar(select(Student.login_code).where(Student.id == uuid.UUID(student_id(0))))
    assert students[0] == {"id": student_id(0), "display_name": STUDENT_NAMES[0], "login_code": saved_code}
    assert all(re.fullmatch(r"[A-HJKMNP-Z2-9]{6}", s["login_code"]) for s in students)
    assert len({s["login_code"] for s in students}) == len(students)  # all different


def test_student_mastery(client, db_session, published_events, release):
    for ticket in ("fractions-add-subtract", "fractions-multiply", "decimals-place-value-rounding"):
        release(ticket)
    # Student 0: NY-5.NF.1 4/4 and NY-5.NF.4 3/3 -> both mastered
    submit(client, "fractions-add-subtract", 0)
    submit(client, "fractions-multiply", 0)
    # Student 1: NY-5.NF.1 3/4 = 75% approaching; NY-5.NBT.3 2/2 mastered; NY-5.NBT.4 2/3 = 66.7% approaching
    submit(client, "fractions-add-subtract", 1, wrong=("nf1-1",))
    submit(client, "decimals-place-value-rounding", 1, wrong=("nbt4-1",))
    # Student 2: NY-5.NF.4 0/3 -> needs support
    submit(client, "fractions-multiply", 2, wrong=("nf4-1", "nf4-2", "nf4-3"))
    other_classroom, other_student = add_other_classroom(db_session)
    release("fractions-multiply", classroom_id=other_classroom)
    assert submit(client, "fractions-multiply", other_student).status_code == 201

    response = get(client, f"/classrooms/{CLASSROOM_ID}/mastery/students")
    assert response.status_code == 200
    body = response.json()
    assert body["classroom_name"] == CLASSROOM_NAME

    by_name = {s["display_name"]: s for s in body["students"]}
    assert [s["display_name"] for s in body["students"]] == sorted(STUDENT_NAMES)  # all 24, sorted

    def cells(index):
        return [
            (c["standard_code"], c["answer_count"], c["correct_count"], c["percent_correct"], c["level"])
            for c in by_name[STUDENT_NAMES[index]]["standards"]
        ]

    assert cells(0) == [("NY-5.NF.1", 4, 4, 100.0, "mastered"), ("NY-5.NF.4", 3, 3, 100.0, "mastered")]
    assert cells(1) == [
        ("NY-5.NBT.3", 2, 2, 100.0, "mastered"),
        ("NY-5.NBT.4", 3, 2, 66.7, "approaching"),
        ("NY-5.NF.1", 4, 3, 75.0, "approaching"),
    ]
    assert cells(2) == [("NY-5.NF.4", 3, 0, 0.0, "needs support")]
    assert cells(3) == []  # never submitted, but still listed

    # Same data, same thresholds: where only student 1 answered, the class view agrees.
    class_view = get(client, f"/classrooms/{CLASSROOM_ID}/mastery").json()
    class_nbt4 = next(s for s in class_view["standards"] if s["standard_code"] == "NY-5.NBT.4")
    assert (class_nbt4["percent_correct"], class_nbt4["level"]) == (66.7, "approaching")


def test_recent_submissions(client, db_session, published_events, release):
    url = f"/classrooms/{CLASSROOM_ID}/recent-submissions"
    assert get(client, url).json() == []

    for ticket in ("fractions-multiply", "decimals-place-value-rounding", "fractions-add-subtract"):
        release(ticket)
    submit(client, "fractions-multiply", 0)
    submit(client, "decimals-place-value-rounding", 1, wrong=("nbt4-1",))
    submit(client, "fractions-add-subtract", 2, wrong=("nf1-1", "nf1-2"))
    other_classroom, other_student = add_other_classroom(db_session)
    release("fractions-multiply", classroom_id=other_classroom)
    submit(client, "fractions-multiply", other_student)

    response = get(client, url)
    assert response.status_code == 200
    feed = response.json()
    # Newest first; the other classroom's submission is left out.
    assert [(f["student_name"], f["ticket_title"], f["score"]) for f in feed] == [
        (STUDENT_NAMES[2], TICKETS_BY_KEY["fractions-add-subtract"]["title"], {"correct": 2, "total": 4, "percent": 50.0}),
        (STUDENT_NAMES[1], TICKETS_BY_KEY["decimals-place-value-rounding"]["title"], {"correct": 4, "total": 5, "percent": 80.0}),
        (STUDENT_NAMES[0], TICKETS_BY_KEY["fractions-multiply"]["title"], {"correct": 3, "total": 3, "percent": 100.0}),
    ]
    assert feed[0]["student_id"] == student_id(2)
    assert feed[0]["ticket_id"] == ticket_id("fractions-add-subtract")
    assert feed[0]["submitted_at"] > feed[1]["submitted_at"] > feed[2]["submitted_at"]

    limited = get(client, url, limit=2).json()
    assert [f["student_name"] for f in limited] == [STUDENT_NAMES[2], STUDENT_NAMES[1]]


@pytest.mark.parametrize("limit", [0, 51, "ten"])
def test_recent_submissions_bad_limit_is_422(client, limit):
    response = get(client, f"/classrooms/{CLASSROOM_ID}/recent-submissions", limit=limit)
    assert response.status_code == 422


@pytest.mark.parametrize("path", ["students", "mastery/students", "recent-submissions", "exit-tickets"])
def test_unknown_classroom_is_404(client, path):
    response = get(client, f"/classrooms/{uuid.uuid4()}/{path}")
    assert response.status_code == 404
