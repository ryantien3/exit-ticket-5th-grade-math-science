import uuid

from seed_data import STANDARDS
from seed_helpers import CLASSROOM_ID, add_other_classroom, student_id, submit, teacher_headers


def test_classroom_mastery(client, db_session, published_events, release):
    for ticket in ("fractions-add-subtract", "fractions-multiply", "decimals-place-value-rounding"):
        release(ticket)
    # NY-5.NF.1: student 0 gets 4/4, student 1 gets 2/4 -> 6/8 = 75.0% approaching
    assert submit(client, "fractions-add-subtract", 0).status_code == 201
    assert submit(client, "fractions-add-subtract", 1, wrong=("nf1-1", "nf1-2")).status_code == 201
    # NY-5.NF.4: 3/3 -> 100.0% mastered
    assert submit(client, "fractions-multiply", 0).status_code == 201
    # NY-5.NBT.3 (0/2) and NY-5.NBT.4 (0/3): everything wrong -> needs support
    assert submit(
        client,
        "decimals-place-value-rounding",
        1,
        wrong=("nbt3-1", "nbt3-3", "nbt4-1", "nbt4-2", "nbt4-5"),
    ).status_code == 201

    # A student in another classroom must not count toward Class 5-201.
    other_classroom, other_student = add_other_classroom(db_session)
    release("fractions-multiply", classroom_id=other_classroom)
    wrong = ("nf4-1", "nf4-2", "nf4-3")
    assert submit(client, "fractions-multiply", other_student, wrong=wrong).status_code == 201

    response = client.get(f"/classrooms/{CLASSROOM_ID}/mastery", headers=teacher_headers())
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
    assert student_id(0) not in response.text  # class view has no per-student data


def test_unknown_classroom_is_404(client):
    response = client.get(f"/classrooms/{uuid.uuid4()}/mastery", headers=teacher_headers())
    assert response.status_code == 404
