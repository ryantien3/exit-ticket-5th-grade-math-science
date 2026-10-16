import uuid

import pytest
from seed_helpers import QUESTIONS_BY_KEY, TICKETS_BY_KEY, question_id, ticket_id

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
    response = client.get("/exit-tickets")
    assert response.status_code == 200
    counts = {t["title"]: t["question_count"] for t in response.json()}
    assert counts == {t["title"]: len(t["questions"]) for t in TICKETS_BY_KEY.values()}


def test_get_exit_ticket_returns_questions_in_order(client):
    response = client.get(f"/exit-tickets/{ticket_id('fractions-add-subtract')}")
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
def test_exit_ticket_never_reveals_answers(client, ticket_key):
    response = client.get(f"/exit-tickets/{ticket_id(ticket_key)}")
    assert response.status_code == 200
    for question in response.json()["questions"]:
        assert question.keys() == {"id", "position", "standard_code", "prompt", "choices"}
    # Belt and braces: no explanation text and no answer-key field names anywhere.
    # (Field names are checked with their JSON quotes; a prompt may say "explanation".)
    strings = set(all_strings(response.json()))
    assert not strings & ALL_EXPLANATIONS
    assert '"correct_choice"' not in response.text
    assert '"explanation"' not in response.text


def test_get_unknown_exit_ticket_is_404(client):
    response = client.get(f"/exit-tickets/{uuid.uuid4()}")
    assert response.status_code == 404
