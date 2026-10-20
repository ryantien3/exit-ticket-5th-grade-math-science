import uuid

import pytest
from seed_helpers import (
    QUESTIONS_BY_KEY,
    TICKETS_BY_KEY,
    add_other_classroom,
    question_id,
    student_headers,
    teacher_headers,
    ticket_id,
)

ALL_EXPLANATIONS = {q["explanation"] for q in QUESTIONS_BY_KEY.values()}


def all_strings(value):
    """Every string anywhere inside a parsed JSON value (keys included)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from all_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from all_strings(item)


def test_list_exit_tickets(client):
    response = client.get("/exit-tickets", headers=teacher_headers())
    assert response.status_code == 200
    counts = {t["title"]: t["question_count"] for t in response.json()}
    assert counts == {t["title"]: len(t["questions"]) for t in TICKETS_BY_KEY.values()}


def test_students_cannot_list_every_exit_ticket(client):
    assert client.get("/exit-tickets", headers=student_headers(0)).status_code == 403


def test_get_exit_ticket_returns_questions_in_order(client, release):
    release("fractions-add-subtract")
    response = client.get(f"/exit-tickets/{ticket_id('fractions-add-subtract')}", headers=student_headers(0))
    assert response.status_code == 200
    body = response.json()
    assert body["title"] == "Adding & Subtracting Fractions"
    keys = TICKETS_BY_KEY["fractions-add-subtract"]["questions"]
    assert [q["id"] for q in body["questions"]] == [question_id(k) for k in keys]
    assert [q["position"] for q in body["questions"]] == [1, 2, 3, 4]
    first = body["questions"][0]
    assert first["choices"] == [
        {"letter": letter, "text": text}
        for letter, text in zip("ABCD", QUESTIONS_BY_KEY[keys[0]]["choices"])
    ]


@pytest.mark.parametrize("ticket_key", list(TICKETS_BY_KEY))
def test_exit_ticket_never_reveals_answers(client, release, ticket_key):
    release(ticket_key)
    response = client.get(f"/exit-tickets/{ticket_id(ticket_key)}", headers=student_headers(0))
    assert response.status_code == 200
    for question in response.json()["questions"]:
        assert question.keys() == {"id", "position", "standard_code", "prompt", "choices"}
    # Belt and braces: no explanation text and no answer-key field names anywhere.
    # (Field names are checked with their JSON quotes; a prompt may say "explanation".)
    strings = set(all_strings(response.json()))
    assert not strings & ALL_EXPLANATIONS
    assert '"correct_choice"' not in response.text
    assert '"explanation"' not in response.text


def test_hidden_exit_ticket_is_404_for_students_but_not_teachers(client, db_session, release):
    url = f"/exit-tickets/{ticket_id('fractions-multiply')}"
    assert client.get(url, headers=student_headers(0)).status_code == 404  # never released

    # Released to a different class: still hidden from Class 5-201.
    other_classroom, _ = add_other_classroom(db_session)
    release("fractions-multiply", classroom_id=other_classroom)
    assert client.get(url, headers=student_headers(0)).status_code == 404

    assert client.get(url, headers=teacher_headers()).status_code == 200  # teachers can preview


def test_get_unknown_exit_ticket_is_404(client):
    response = client.get(f"/exit-tickets/{uuid.uuid4()}", headers=teacher_headers())
    assert response.status_code == 404
