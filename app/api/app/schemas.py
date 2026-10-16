"""Request and response shapes for the API (Pydantic models).

Response models work as an allowlist: FastAPI sends only the fields listed here,
so anything else on a database row (like a question's correct answer) can't leak.
"""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Letter = Literal["A", "B", "C", "D"]
LETTERS = "ABCD"  # choices[0] is "A", choices[1] is "B", ...

MasteryLevel = Literal["needs support", "approaching", "mastered"]


class StandardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    domain: str
    subject: str
    description: str


class ExitTicketSummary(BaseModel):
    id: uuid.UUID
    title: str
    subject: str
    question_count: int


class Choice(BaseModel):
    letter: Letter
    text: str


class StudentQuestion(BaseModel):
    """A question as a student sees it: deliberately no correct answer or explanation."""

    id: uuid.UUID
    position: int
    standard_code: str
    prompt: str
    choices: list[Choice]


class ExitTicketDetail(BaseModel):
    id: uuid.UUID
    title: str
    subject: str
    questions: list[StudentQuestion]


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: uuid.UUID
    chosen_choice: Letter


class SubmissionIn(BaseModel):
    # extra="forbid": unexpected fields (e.g. a client sending is_correct) are rejected.
    model_config = ConfigDict(extra="forbid")

    # TODO: once there's sign-in, take the student from the login token, not the body.
    student_id: uuid.UUID
    answers: list[AnswerIn] = Field(min_length=1)


class Score(BaseModel):
    correct: int
    total: int
    percent: float


class QuestionResult(BaseModel):
    question_id: uuid.UUID
    chosen_choice: Letter
    correct_choice: Letter
    is_correct: bool
    explanation: str


class StandardScore(BaseModel):
    standard_code: str
    correct: int
    total: int
    percent: float


class SubmissionOut(BaseModel):
    score: Score
    results: list[QuestionResult]
    standards: list[StandardScore]


class StandardMastery(BaseModel):
    standard_code: str
    subject: str
    description: str
    answer_count: int
    correct_count: int
    percent_correct: float | None  # None when no answers yet
    level: MasteryLevel | None  # None when no answers yet


class ClassroomMastery(BaseModel):
    classroom_id: uuid.UUID
    classroom_name: str
    standards: list[StandardMastery]
