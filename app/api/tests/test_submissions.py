import uuid

import pytest
from sqlalchemy import select

from app import events
from app.models import Answer
from seed_helpers import (
    CLASSROOM_ID,
    QUESTIONS_BY_KEY,
    TICKETS_BY_KEY,
    answers_for,
    question_id,
    student_headers,
    student_id,
    teacher_headers,
    ticket_id,
)
from seed_helpers import submit as submit_ticket

# Mixes two standards (2 x NY-5.NBT.3, 3 x NY-5.NBT.4), good for the per-standard summary.
TICKET = "decimals-place-value-rounding"
URL = f"/exit-tickets/{ticket_id(TICKET)}/submissions"


@pytest.fixture(autouse=True)
def released(release):
    """Every test in this file starts with TICKET released to Class 5-201."""
    release(TICKET)


def post(client, answers, student=0, extra=None):
    return client.post(URL, json={"answers": answers, **(extra or {})}, headers=student_headers(student))


def test_submission_is_graded_saved_and_published(client, db_session, published_events):
    response = submit_ticket(client, TICKET, 0, wrong=("nbt4-2",))

    assert response.status_code == 201
    result = response.json()
    assert result["score"] == {"correct": 4, "total": 5, "percent": 80.0}
    assert result["standards"] == [
        {"standard_code": "NY-5.NBT.3", "correct": 2, "total": 2, "percent": 100.0},
        {"standard_code": "NY-5.NBT.4", "correct": 2, "total": 3, "percent": 66.7},
    ]

    # Results come back in ticket order; the missed question shows the right answer.
    keys = TICKETS_BY_KEY[TICKET]["questions"]
    assert [r["question_id"] for r in result["results"]] == [question_id(k) for k in keys]
    missed = result["results"][keys.index("nbt4-2")]
    assert missed["is_correct"] is False
    assert missed["correct_choice"] == QUESTIONS_BY_KEY["nbt4-2"]["correct"]
    assert missed["explanation"] == QUESTIONS_BY_KEY["nbt4-2"]["explanation"]

    # One saved row per question, saved under the signed-in student.
    rows = db_session.scalars(
        select(Answer).where(Answer.student_id == uuid.UUID(student_id(0)))
    ).all()
    assert len(rows) == 5
    assert sum(row.is_correct for row in rows) == 4
    assert {row.standard_code for row in rows} == {"NY-5.NBT.3", "NY-5.NBT.4"}

    # One event per saved answer, matching the rows.
    assert len(published_events) == 5
    assert {e.answer_id for e in published_events} == {row.id for row in rows}
    assert all(e.classroom_id == CLASSROOM_ID for e in published_events)
    assert len({e.answered_at for e in published_events}) == 1


def test_publish_failure_does_not_fail_submission(client, db_session, monkeypatch):
    def broken_publisher(event):
        raise RuntimeError("simulated Pub/Sub outage")

    monkeypatch.setattr(events, "publish_answer_event", broken_publisher)
    response = submit_ticket(client, TICKET, 1)

    assert response.status_code == 201
    saved = db_session.scalars(select(Answer).where(Answer.student_id == uuid.UUID(student_id(1))))
    assert len(saved.all()) == 5


def test_second_submission_is_409(client, published_events):
    assert submit_ticket(client, TICKET, 2).status_code == 201

    response = submit_ticket(client, TICKET, 2)
    assert response.status_code == 409
    assert "already submitted" in response.json()["detail"]
    assert len(published_events) == 5  # nothing published for the rejected attempt


def test_cannot_submit_as_someone_else(client, db_session):
    # The student comes from the token; a student_id in the body is rejected outright.
    response = post(client, answers_for(TICKET), student=0, extra={"student_id": student_id(1)})
    assert response.status_code == 422
    assert db_session.scalars(select(Answer)).first() is None


def test_sign_in_required(client):
    response = client.post(URL, json={"answers": answers_for(TICKET)})
    assert response.status_code == 401


def test_teachers_cannot_submit(client):
    response = client.post(URL, json={"answers": answers_for(TICKET)}, headers=teacher_headers())
    assert response.status_code == 403


def test_hidden_ticket_cannot_be_submitted(client, db_session):
    # Released earlier, then hidden again by the teacher while the student was answering.
    hide = client.delete(
        f"/classrooms/{CLASSROOM_ID}/exit-tickets/{ticket_id(TICKET)}/release", headers=teacher_headers()
    )
    assert hide.status_code == 204
    assert post(client, answers_for(TICKET)).status_code == 404
    assert db_session.scalars(select(Answer)).first() is None


def test_unknown_ticket_is_404(client):
    response = client.post(
        f"/exit-tickets/{uuid.uuid4()}/submissions",
        json={"answers": answers_for(TICKET)},
        headers=student_headers(0),
    )
    assert response.status_code == 404


def test_question_not_on_ticket_is_422(client):
    answers = answers_for(TICKET)
    answers[0] = {"question_id": question_id("nf1-1"), "chosen_choice": "B"}  # a fractions question
    response = post(client, answers)
    assert response.status_code == 422
    assert "not on this exit ticket" in response.json()["detail"]
    assert question_id("nf1-1") in response.json()["detail"]


def test_duplicate_question_is_422(client):
    answers = answers_for(TICKET)
    answers.append(dict(answers[0]))
    response = post(client, answers)
    assert response.status_code == 422
    assert "more than once" in response.json()["detail"]


def test_missing_question_is_422(client, db_session):
    response = post(client, answers_for(TICKET)[:-1])
    assert response.status_code == 422
    assert "Every question must be answered" in response.json()["detail"]
    assert db_session.scalars(select(Answer)).first() is None  # nothing saved


@pytest.mark.parametrize(
    "bad_body",
    [
        pytest.param({}, id="missing answers"),
        pytest.param({"answers": []}, id="empty answers"),
        pytest.param(
            {"answers": [{"question_id": str(uuid.uuid4()), "chosen_choice": "E"}]},
            id="choice not A-D",
        ),
        pytest.param(
            {"answers": [{"question_id": "not-a-uuid", "chosen_choice": "A"}]},
            id="bad uuid",
        ),
        pytest.param(
            {"answers": [{"question_id": str(uuid.uuid4()), "chosen_choice": "A", "is_correct": True}]},
            id="client sends is_correct",
        ),
    ],
)
def test_malformed_body_is_422(client, bad_body):
    response = client.post(URL, json=bad_body, headers=student_headers(0))
    assert response.status_code == 422
