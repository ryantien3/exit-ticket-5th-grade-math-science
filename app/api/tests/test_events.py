"""The answer-event publisher: contract with the Avro schema, no-op mode, failure handling, ordering.

No network: the Pub/Sub client is replaced by FakePublisherClient, which returns real
concurrent.futures.Future objects, so callbacks run exactly as they do with the real library.
"""

import io
import json
import logging
import uuid
from concurrent import futures
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import fastavro
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app import events
from app.logging_config import JsonFormatter
from app.models import Answer, Student
from seed_helpers import student_id, submit, ticket_id

SCHEMA_PATH = (
    Path(__file__).resolve().parents[3] / "infra" / "terraform" / "analytics" / "schemas" / "answer_event.avsc"
)
SCHEMA_JSON = json.loads(SCHEMA_PATH.read_text())
SCHEMA_FIELDS = [field["name"] for field in SCHEMA_JSON["fields"]]
PARSED_SCHEMA = fastavro.parse_schema(SCHEMA_JSON)

TOPIC = "projects/test-project-1/topics/answer-events"
TICKET = "decimals-place-value-rounding"  # 5 questions, NY-5.NBT.3 and NY-5.NBT.4 (math)

# The same instant as the 6A design doc's example message.
ANSWERED_AT = datetime(2026, 11, 18, 14, 5, 30, 123456, tzinfo=timezone.utc)
ANSWERED_AT_MICROS = 1795010730123456


def make_event(**overrides) -> events.AnswerEvent:
    values = dict(
        answered_at=ANSWERED_AT,
        answer_id=uuid.uuid4(),
        student_id=uuid.uuid4(),
        classroom_id=uuid.uuid4(),
        ticket_id=uuid.uuid4(),
        question_id=uuid.uuid4(),
        standard_code="NY-5.NF.4",
        standard_domain="NY-5.NF",
        subject="math",
        chosen_choice="C",
        is_correct=True,
    )
    values.update(overrides)
    return events.AnswerEvent(**values)


class FakePublisherClient:
    """Stands in for pubsub_v1.PublisherClient. Records every publish; outcome is configurable."""

    def __init__(self, outcome: str = "success", on_publish=None):
        self.outcome = outcome  # "success", "fail" (future fails later), "raise" (publish() raises), "hang"
        self.on_publish = on_publish
        self.published: list[tuple[str, bytes]] = []
        self.stopped = False

    def publish(self, topic: str, data: bytes) -> futures.Future:
        if self.on_publish:
            self.on_publish()
        if self.outcome == "raise":
            raise RuntimeError("Cannot publish on a stopped publisher.")
        self.published.append((topic, data))
        future: futures.Future = futures.Future()
        if self.outcome == "success":
            future.set_result(f"msg-{len(self.published)}")
        elif self.outcome == "fail":
            future.set_exception(RuntimeError("400 INVALID_ARGUMENT: Message failed schema validation"))
        return future  # "hang": never resolves

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def clean_publisher(monkeypatch):
    """Every test starts with no client, and module state is restored afterwards."""
    monkeypatch.setattr(events, "_publisher", None)
    monkeypatch.setattr(events, "_topic", None)
    monkeypatch.setattr(events, "_pending", set())


def start_fake(monkeypatch, fake: FakePublisherClient) -> FakePublisherClient:
    monkeypatch.setattr(events.pubsub_v1, "PublisherClient", lambda: fake)
    events.start_publisher(TOPIC)
    return fake


def log_text(caplog) -> str:
    """Every captured record exactly as it would reach Cloud Logging (JSON, incl. json_fields)."""
    formatter = JsonFormatter()
    return "\n".join(formatter.format(record) for record in caplog.records)


def records_with(caplog, message: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.getMessage() == message]


# --- The contract with infra/terraform/analytics/schemas/answer_event.avsc ---


def test_model_fields_match_the_avro_schema_exactly():
    # Same names, same order. Adding a field to one but not the other fails here.
    assert list(events.AnswerEvent.model_fields) == SCHEMA_FIELDS


def test_message_decodes_with_the_avro_schema():
    event = make_event()
    message = event.to_message()

    body = json.loads(message)
    assert list(body) == SCHEMA_FIELDS  # no extra keys, none missing
    assert body["answered_at"] == ANSWERED_AT_MICROS  # integer microseconds, no float rounding
    assert body["event_type"] == "answer_submitted"
    assert body["schema_version"] == 2

    # fastavro reads Avro's JSON encoding and fails on a wrong type or a missing field.
    [record] = fastavro.json_reader(io.StringIO(message.decode()), PARSED_SCHEMA)
    assert record["answered_at"] == ANSWERED_AT
    assert record["student_id"] == str(event.student_id)
    assert record["is_correct"] is True


def test_same_answer_gets_the_same_event_id_in_every_build():
    # The app at submit time and a republish from Cloud SQL build separate objects for one answer:
    # they must agree, or the view's dedup (PARTITION BY event_id) counts the answer twice.
    answer_id = uuid.uuid4()
    first = make_event(answer_id=answer_id)
    rebuilt = make_event(answer_id=answer_id)
    assert first.event_id == rebuilt.event_id == events.answer_event_id(answer_id)
    assert json.loads(first.to_message())["event_id"] == json.loads(rebuilt.to_message())["event_id"]


def test_different_answers_get_different_event_ids():
    ids = {make_event().event_id for _ in range(100)}  # make_event uses a fresh answer_id each time
    assert len(ids) == 100


def test_event_id_namespace_never_changes():
    # Pinned: changing EVENT_NAMESPACE would re-key every answer and break dedup of republishes.
    answer_id = uuid.UUID("b4b4e164-3866-4de5-a365-a44d2a8360ee")
    assert events.answer_event_id(answer_id) == uuid.UUID("61fb0c0f-69bd-5b99-a135-3716ec91bc80")
    assert events.answer_event_id(answer_id).version == 5


def test_an_explicit_event_id_must_be_the_derived_one():
    answer_id = uuid.uuid4()
    assert make_event(answer_id=answer_id, event_id=events.answer_event_id(answer_id)).event_id
    with pytest.raises(ValidationError, match="answer_event_id"):
        make_event(answer_id=answer_id, event_id=uuid.uuid4())


@pytest.mark.parametrize("field", ["display_name", "login_code"])
def test_a_name_or_sign_in_code_cannot_be_added(field):
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        make_event(**{field: "Avery B." if field == "display_name" else "ABC234"})


def test_naive_timestamp_is_rejected():
    with pytest.raises(ValidationError, match="timezone-aware"):
        make_event(answered_at=datetime(2026, 11, 18, 14, 5, 30))


# --- No-op mode: PUBSUB_TOPIC unset ---


@pytest.mark.parametrize("topic", [None, ""])
def test_no_topic_creates_no_client_and_logs_once(monkeypatch, caplog, topic):
    client_class = mock.Mock()
    monkeypatch.setattr(events.pubsub_v1, "PublisherClient", client_class)
    caplog.set_level(logging.INFO)

    events.start_publisher(topic)
    events.publish_answer_event(make_event())
    events.stop_publisher()

    client_class.assert_not_called()
    assert len(records_with(caplog, "PUBSUB_TOPIC is not set: answer events will be logged, not published")) == 1
    assert len(records_with(caplog, "answer_event")) == 1  # the event is still logged


# --- Publishing and its outcomes ---


def test_publish_sends_the_message_and_logs_the_message_id(monkeypatch, caplog):
    fake = start_fake(monkeypatch, FakePublisherClient("success"))
    caplog.set_level(logging.INFO)
    event = make_event()

    events.publish_answer_event(event)

    assert fake.published == [(TOPIC, event.to_message())]
    [record] = records_with(caplog, "answer_event_published")
    assert record.json_fields == {"event_id": str(event.event_id), "message_id": "msg-1"}
    assert events._pending == set()  # the finished future was forgotten


def test_failed_publish_is_logged_without_student_data(monkeypatch, caplog):
    start_fake(monkeypatch, FakePublisherClient("fail"))
    event = make_event()

    events.publish_answer_event(event)  # must not raise

    [record] = records_with(caplog, "answer_event_publish_failed")
    assert record.levelno == logging.ERROR
    assert record.json_fields["event_id"] == str(event.event_id)
    assert "INVALID_ARGUMENT" in record.json_fields["error"]
    text = log_text(caplog)
    for value in (event.student_id, event.answer_id, event.classroom_id, event.question_id):
        assert str(value) not in text


def test_publish_that_raises_immediately_is_logged_not_raised(monkeypatch, caplog):
    start_fake(monkeypatch, FakePublisherClient("raise"))

    events.publish_answer_event(make_event())  # must not raise

    [record] = records_with(caplog, "answer_event_publish_failed")
    assert record.json_fields["error_type"] == "RuntimeError"


def test_stop_flushes_and_waits_for_messages_in_flight(monkeypatch, caplog):
    fake = start_fake(monkeypatch, FakePublisherClient("hang"))
    monkeypatch.setattr(events, "SHUTDOWN_TIMEOUT_SECONDS", 0.05)
    events.publish_answer_event(make_event())

    events.stop_publisher()

    assert fake.stopped
    [record] = records_with(caplog, "answer_events_unconfirmed_at_shutdown")
    assert record.json_fields == {"count": 1}
    assert events._publisher is None


# --- Through the real endpoint ---


@pytest.fixture
def released(release):
    release(TICKET)


def test_submission_publishes_one_event_per_answer_after_the_commit(
    client, db_session, released, monkeypatch
):
    order: list[str] = []
    fake = start_fake(monkeypatch, FakePublisherClient(on_publish=lambda: order.append("publish")))
    real_commit = db_session.commit

    def recording_commit():
        order.append("commit")
        real_commit()

    monkeypatch.setattr(db_session, "commit", recording_commit)

    response = submit(client, TICKET, 3)

    assert response.status_code == 201
    assert order == ["commit"] + ["publish"] * 5  # nothing is sent before the answers are saved

    rows = db_session.scalars(select(Answer).where(Answer.student_id == uuid.UUID(student_id(3)))).all()
    bodies = [json.loads(data) for _, data in fake.published]
    assert {topic for topic, _ in fake.published} == {TOPIC}
    assert {body["answer_id"] for body in bodies} == {str(row.id) for row in rows}
    # Each event_id is the deterministic one for its saved answer.
    assert {body["event_id"] for body in bodies} == {str(events.answer_event_id(row.id)) for row in rows}
    assert {body["ticket_id"] for body in bodies} == {ticket_id(TICKET)}
    assert {(body["standard_domain"], body["subject"]) for body in bodies} == {("NY-5.NBT", "math")}
    for _, data in fake.published:
        list(fastavro.json_reader(io.StringIO(data.decode()), PARSED_SCHEMA))  # each one valid


def test_payload_has_no_name_or_sign_in_code(client, db_session, released, monkeypatch):
    fake = start_fake(monkeypatch, FakePublisherClient())

    assert submit(client, TICKET, 4).status_code == 201

    student = db_session.get(Student, uuid.UUID(student_id(4)))
    for _, data in fake.published:
        text = data.decode()
        assert student.display_name not in text
        assert student.login_code not in text
        assert list(json.loads(text)) == SCHEMA_FIELDS


def test_rejected_submission_publishes_nothing(client, released, monkeypatch):
    fake = start_fake(monkeypatch, FakePublisherClient())
    assert submit(client, TICKET, 5).status_code == 201
    assert submit(client, TICKET, 5).status_code == 409  # duplicate: nothing saved, nothing sent
    assert len(fake.published) == 5
