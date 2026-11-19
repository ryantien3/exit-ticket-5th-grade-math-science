"""Answer events: the hand-off point between the API and analytics.

Endpoints call publish_answer_event() after the database commit and don't know or care where the
event goes. With PUBSUB_TOPIC set, it's published to Pub/Sub, and a BigQuery subscription writes it
to exit_ticket_analytics.answer_events. Without it (local dev, tests), it's only logged.

AnswerEvent is the contract with that pipeline. It must match
infra/terraform/analytics/schemas/answer_event.avsc field for field (tests/test_events.py checks).
Change both deliberately, and bump schema_version when you do. See docs/phase-6a-design.md §4.

Publishing never blocks or fails a request: publish() hands the message to the client library's
background thread and returns at once; the result (message ID or error) is logged by a callback.
"""

import json
import logging
import threading
import uuid
from concurrent import futures
from datetime import datetime, timedelta, timezone
from typing import Literal

from google.cloud import pubsub_v1
from pydantic import BaseModel, ConfigDict, Field, field_validator

logger = logging.getLogger(__name__)

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

# How long shutdown waits for messages still in flight. Kubernetes allows 30 s by default.
SHUTDOWN_TIMEOUT_SECONDS = 10


class AnswerEvent(BaseModel):
    # forbid: a stray field (a name, a login code) is an error, not silently sent. The Pub/Sub schema
    # ignores unknown keys (6A §6 test 4), so this model is what keeps them out.
    model_config = ConfigDict(extra="forbid", frozen=True)

    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)  # made once; retries reuse it
    event_type: Literal["answer_submitted"] = "answer_submitted"
    schema_version: Literal[2] = 2
    answered_at: datetime
    answer_id: uuid.UUID
    student_id: uuid.UUID
    classroom_id: uuid.UUID
    ticket_id: uuid.UUID
    question_id: uuid.UUID
    standard_code: str
    standard_domain: str
    subject: Literal["math", "science"]
    chosen_choice: Literal["A", "B", "C", "D"]
    is_correct: bool

    @field_validator("answered_at")
    @classmethod
    def must_be_utc_aware(cls, value: datetime) -> datetime:
        # A naive datetime would be turned into the wrong instant (local time read as UTC).
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("answered_at must be timezone-aware")
        return value

    def to_message(self) -> bytes:
        """The Pub/Sub message body: JSON in the Avro schema's JSON encoding.

        UUIDs are strings; answered_at (Avro timestamp-micros) is an integer number of microseconds
        since 1970-01-01 UTC, computed with integers so no precision is lost to floats.
        """
        body = self.model_dump(mode="json")
        body["answered_at"] = (self.answered_at - EPOCH) // timedelta(microseconds=1)
        return json.dumps(body, separators=(",", ":")).encode("utf-8")


# --- The publisher: one client per process, started and stopped by the app's lifespan ---

_publisher: pubsub_v1.PublisherClient | None = None
_topic: str | None = None
_pending: set[futures.Future] = set()
_pending_lock = threading.Lock()


def start_publisher(topic: str | None) -> None:
    """Create the client if a topic is configured. Credentials: Application Default Credentials."""
    global _publisher, _topic
    if not topic:
        logger.info("PUBSUB_TOPIC is not set: answer events will be logged, not published")
        return
    # No credentials passed: the library finds them itself (ADC). On the Mac that's your
    # `gcloud auth application-default login`; on GKE it's the pod's Workload Identity.
    _publisher = pubsub_v1.PublisherClient()
    _topic = topic
    logger.info("Publishing answer events to %s", topic)


def stop_publisher() -> None:
    """Send what's still batched, wait briefly for it, and release the client."""
    global _publisher, _topic
    if _publisher is None:
        return
    _publisher.stop()  # flushes the current batch; publish() raises after this
    with _pending_lock:
        in_flight = set(_pending)
    _, not_done = futures.wait(in_flight, timeout=SHUTDOWN_TIMEOUT_SECONDS)
    if not_done:
        logger.warning(
            "answer_events_unconfirmed_at_shutdown",
            extra={"json_fields": {"count": len(not_done)}},
        )
    _publisher = None
    _topic = None


def publish_answer_event(event: AnswerEvent) -> None:
    """Hand one event to Pub/Sub without waiting. Never raises."""
    if _publisher is None or _topic is None:
        logger.info("answer_event", extra={"json_fields": {"event": event.model_dump(mode="json")}})
        return
    try:
        future = _publisher.publish(_topic, event.to_message())
    except Exception as exc:  # e.g. RuntimeError after stop(), message too large
        _log_failure(event.event_id, exc)
        return
    with _pending_lock:
        _pending.add(future)
    future.add_done_callback(lambda done: _on_publish_done(event.event_id, done))


def _on_publish_done(event_id: uuid.UUID, future: futures.Future) -> None:
    # Runs on the client library's thread, not the request's.
    with _pending_lock:
        _pending.discard(future)
    try:
        message_id = future.result()
    except Exception as exc:  # schema rejection (INVALID_ARGUMENT), permission, timeout...
        _log_failure(event_id, exc)
        return
    logger.info(
        "answer_event_published",
        extra={"json_fields": {"event_id": str(event_id), "message_id": message_id}},
    )


def _log_failure(event_id: uuid.UUID, exc: BaseException) -> None:
    # Only the event_id identifies the event: no student or answer data in the log. The event_id
    # is enough to find it later (and the answer itself is safe in Cloud SQL).
    logger.error(
        "answer_event_publish_failed",
        extra={
            "json_fields": {
                "event_id": str(event_id),
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
            }
        },
    )
