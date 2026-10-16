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
    student_id,
    ticket_id,
)

# Mixes two standards (2 x NY-5.NBT.3, 3 x NY-5.NBT.4), good for the per-standard summary.
TICKET = "decimals-place-value-rounding"


def submit(client, ticket_key, body):
    return client.post(f"/exit-tickets/{ticket_id(ticket_key)}/submissions", json=body)


def test_submission_is_graded_saved_and_published(client, db_session, published_events):
    body = {"student_id": student_id(0), "answers": answers_for(TICKET, wrong=("nbt4-2",))}
    response = submit(client, TICKET, body)

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

    # One saved row per question, with standard_code copied from the question.
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
    body = {"student_id": student_id(1), "answers": answers_for(TICKET)}
    response = submit(client, TICKET, body)

    assert response.status_code == 201
    saved = db_session.scalars(select(Answer).where(Answer.student_id == uuid.UUID(student_id(1))))
    assert len(saved.all()) == 5


def test_second_submission_is_409(client, published_events):
    body = {"student_id": student_id(2), "answers": answers_for(TICKET)}
    assert submit(client, TICKET, body).status_code == 201

    response = submit(client, TICKET, body)
    assert response.status_code == 409
    assert "already submitted" in response.json()["detail"]
    assert len(published_events) == 5  # nothing published for the rejected attempt


def test_unknown_ticket_is_404(client):
    response = client.post(
        f"/exit-tickets/{uuid.uuid4()}/submissions",
        json={"student_id": student_id(0), "answers": answers_for(TICKET)},
    )
    assert response.status_code == 404


def test_unknown_student_is_422(client):
    body = {"student_id": str(uuid.uuid4()), "answers": answers_for(TICKET)}
    response = submit(client, TICKET, body)
    assert response.status_code == 422
    assert "Unknown student" in response.json()["detail"]


def test_question_not_on_ticket_is_422(client):
    answers = answers_for(TICKET)
    answers[0] = {"question_id": question_id("nf1-1"), "chosen_choice": "B"}  # a fractions question
    response = submit(client, TICKET, {"student_id": student_id(0), "answers": answers})
    assert response.status_code == 422
    assert "not on this exit ticket" in response.json()["detail"]
    assert question_id("nf1-1") in response.json()["detail"]


def test_duplicate_question_is_422(client):
    answers = answers_for(TICKET)
    answers.append(dict(answers[0]))
    response = submit(client, TICKET, {"student_id": student_id(0), "answers": answers})
    assert response.status_code == 422
    assert "more than once" in response.json()["detail"]


def test_missing_question_is_422(client, db_session):
    answers = answers_for(TICKET)[:-1]
    response = submit(client, TICKET, {"student_id": student_id(0), "answers": answers})
    assert response.status_code == 422
    assert "Every question must be answered" in response.json()["detail"]
    assert db_session.scalars(select(Answer)).first() is None  # nothing saved


@pytest.mark.parametrize(
    "bad_body",
    [
        pytest.param({"answers": []}, id="missing student_id"),
        pytest.param({"student_id": "not-a-uuid", "answers": []}, id="bad uuid"),
        pytest.param({"student_id": str(uuid.uuid4()), "answers": []}, id="empty answers"),
        pytest.param(
            {"student_id": str(uuid.uuid4()), "answers": [{"question_id": str(uuid.uuid4()), "chosen_choice": "E"}]},
            id="choice not A-D",
        ),
        pytest.param(
            {
                "student_id": str(uuid.uuid4()),
                "answers": [{"question_id": str(uuid.uuid4()), "chosen_choice": "A", "is_correct": True}],
            },
            id="client sends is_correct",
        ),
    ],
)
def test_malformed_body_is_422(client, bad_body):
    response = submit(client, TICKET, bad_body)
    assert response.status_code == 422
