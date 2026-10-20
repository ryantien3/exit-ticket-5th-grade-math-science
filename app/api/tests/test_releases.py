"""Teachers release and hide exit tickets per classroom; students only ever see released ones."""

import uuid

from seed_helpers import (
    CLASSROOM_ID,
    TICKETS_BY_KEY,
    add_other_classroom,
    student_headers,
    submit,
    teacher_headers,
    ticket_id,
)


def release_url(ticket_key, classroom_id=CLASSROOM_ID):
    return f"/classrooms/{classroom_id}/exit-tickets/{ticket_id(ticket_key)}/release"


def statuses(client):
    response = client.get(f"/classrooms/{CLASSROOM_ID}/exit-tickets", headers=teacher_headers())
    assert response.status_code == 200
    return {t["id"]: t for t in response.json()}


def my_tickets(client, student=0):
    response = client.get("/me/exit-tickets", headers=student_headers(student))
    assert response.status_code == 200
    return response.json()


def test_everything_starts_hidden(client):
    tickets = statuses(client)
    assert len(tickets) == len(TICKETS_BY_KEY)
    assert all(t["released"] is False and t["released_at"] is None for t in tickets.values())
    assert my_tickets(client) == []


def test_release_and_hide(client):
    response = client.put(release_url("fractions-multiply"), headers=teacher_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == ticket_id("fractions-multiply")
    assert body["released"] is True
    assert body["question_count"] == 3
    first_released_at = body["released_at"]

    # Releasing again is harmless and keeps the original time.
    again = client.put(release_url("fractions-multiply"), headers=teacher_headers()).json()
    assert again["released_at"] == first_released_at
    assert statuses(client)[ticket_id("fractions-multiply")]["released"] is True

    assert client.delete(release_url("fractions-multiply"), headers=teacher_headers()).status_code == 204
    assert statuses(client)[ticket_id("fractions-multiply")]["released"] is False
    # Hiding something already hidden is harmless too.
    assert client.delete(release_url("fractions-multiply"), headers=teacher_headers()).status_code == 204


def test_students_see_only_their_classes_released_tickets(client, db_session, release, published_events):
    release("fractions-multiply")
    release("decimals-operations")
    other_classroom, _ = add_other_classroom(db_session)
    release("matter-properties-mixing", classroom_id=other_classroom)

    tickets = my_tickets(client)
    # Newest release first.
    assert [t["id"] for t in tickets] == [ticket_id("decimals-operations"), ticket_id("fractions-multiply")]
    assert tickets[1] == {
        "id": ticket_id("fractions-multiply"),
        "title": TICKETS_BY_KEY["fractions-multiply"]["title"],
        "subject": "math",
        "question_count": 3,
        "submitted": False,
    }

    # "submitted" is per student.
    assert submit(client, "fractions-multiply", 0).status_code == 201
    assert {t["id"]: t["submitted"] for t in my_tickets(client, 0)}[ticket_id("fractions-multiply")] is True
    assert {t["id"]: t["submitted"] for t in my_tickets(client, 1)}[ticket_id("fractions-multiply")] is False


def test_hidden_again_disappears_for_students(client, release):
    release("fractions-multiply")
    assert len(my_tickets(client)) == 1
    client.delete(release_url("fractions-multiply"), headers=teacher_headers())
    assert my_tickets(client) == []


def test_release_unknown_classroom_or_ticket_is_404(client):
    unknown_classroom = f"/classrooms/{uuid.uuid4()}/exit-tickets/{ticket_id('fractions-multiply')}/release"
    unknown_ticket = f"/classrooms/{CLASSROOM_ID}/exit-tickets/{uuid.uuid4()}/release"
    for url in (unknown_classroom, unknown_ticket):
        assert client.put(url, headers=teacher_headers()).status_code == 404
        assert client.delete(url, headers=teacher_headers()).status_code == 404
