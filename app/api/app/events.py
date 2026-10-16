"""Answer events: the hand-off point between the API and analytics.

Endpoints call publish_answer_event() and don't know or care where the event goes.
Today it's written to the log; in Phase 6 only this file changes, to publish to
Pub/Sub (which feeds BigQuery). AnswerEvent is the contract with that pipeline:
change its fields deliberately, and bump schema_version when you do.
"""

import logging
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AnswerEvent(BaseModel):
    schema_version: Literal[1] = 1
    answer_id: uuid.UUID
    student_id: uuid.UUID
    classroom_id: uuid.UUID
    ticket_id: uuid.UUID
    question_id: uuid.UUID
    standard_code: str
    chosen_choice: str
    is_correct: bool
    answered_at: datetime


def publish_answer_event(event: AnswerEvent) -> None:
    # Phase 6: replace this body with a Pub/Sub publish. Callers stay the same.
    logger.info("answer_event", extra={"json_fields": {"event": event.model_dump(mode="json")}})
