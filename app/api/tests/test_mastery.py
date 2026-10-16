import uuid

from app.models import Classroom, Student
from seed_data import STANDARDS
from seed_helpers import CLASSROOM_ID, answers_for, student_id, ticket_id


def submit(client, ticket_key, student, wrong=()):
    response = client.post(
        f"/exit-tickets/{ticket_id(ticket_key)}/submissions",
        json={"student_id": student, "answers": answers_for(ticket_key, wrong=wrong)},
    )
    assert response.status_code == 201


def test_classroom_mastery(client, db_session, published_events):
    # NY-5.NF.1: student 0 gets 4/4, student 1 gets 2/4 -> 6/8 = 75.0% approaching
    submit(client, "fractions-add-subtract", student_id(0))
    submit(client, "fractions-add-subtract", student_id(1), wrong=("nf1-1", "nf1-2"))
    # NY-5.NF.4: 3/3 -> 100.0% mastered
    submit(client, "fractions-multiply", student_id(0))
    # NY-5.NBT.3 (0/2) and NY-5.NBT.4 (0/3): everything wrong -> needs support
    submit(
        client,
        "decimals-place-value-rounding",
        student_id(1),
        wrong=("nbt3-1", "nbt3-3", "nbt4-1", "nbt4-2", "nbt4-5"),
    )

    # A student in another classroom must not count toward Class 5-201.
    other_classroom = Classroom(id=uuid.uuid4(), name="Class 5-999")
    other_student = Student(id=uuid.uuid4(), classroom_id=other_classroom.id, display_name="Test Z.")
    db_session.add(other_classroom)
    db_session.flush()
    db_session.add(other_student)
    db_session.flush()
    submit(client, "fractions-multiply", str(other_student.id), wrong=("nf4-1", "nf4-2", "nf4-3"))

    response = client.get(f"/classrooms/{CLASSROOM_ID}/mastery")
    assert response.status_code == 200
    body = response.json()
    assert body["classroom_name"] == "Class 5-201"

    by_code = {s["standard_code"]: s for s in body["standards"]}
    assert set(by_code) == {s["code"] for s in STANDARDS}  # every standard listed

    def summary(code):
        s = by_code[code]
        return s["answer_count"], s["correct_count"], s["percent_correct"], s["level"]

    assert summary("NY-5.NF.1") == (8, 6, 75.0, "approaching")
    assert summary("NY-5.NF.4") == (3, 3, 100.0, "mastered")
    assert summary("NY-5.NBT.3") == (2, 0, 0.0, "needs support")
    assert summary("NY-5.NBT.4") == (3, 0, 0.0, "needs support")
    assert summary("5-PS1-1") == (0, 0, None, None)  # no answers yet


def test_unknown_classroom_is_404(client):
    response = client.get(f"/classrooms/{uuid.uuid4()}/mastery")
    assert response.status_code == 404
