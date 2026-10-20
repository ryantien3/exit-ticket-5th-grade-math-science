"""Request and response shapes for the API (Pydantic models).

Response models work as an allowlist: FastAPI sends only the fields listed here,
so anything else on a database row (like a question's correct answer) can't leak.
"""

import uuid
from datetime import datetime
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


class StudentExitTicket(ExitTicketSummary):
    """A released exit ticket, as one student sees it in their list."""

    submitted: bool


class ClassroomExitTicketStatus(ExitTicketSummary):
    """An exit ticket, as a teacher sees it for one classroom."""

    released: bool
    released_at: datetime | None  # None while hidden


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
    # extra="forbid": unexpected fields (e.g. a client sending is_correct or student_id) are rejected.
    # There's no student_id: the student comes from the sign-in token, so nobody can submit as someone else.
    model_config = ConfigDict(extra="forbid")

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


class ClassroomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str


class StudentOut(BaseModel):
    """Teacher-only: includes the student's secret login code."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    login_code: str


class StudentLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_code: str = Field(min_length=1, max_length=20)


class TeacherLoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passcode: str = Field(min_length=1, max_length=200)


class StudentSessionOut(BaseModel):
    token: str
    expires_at: datetime
    display_name: str
    classroom_name: str


class TeacherSessionOut(BaseModel):
    token: str
    expires_at: datetime


class StudentStandardResult(BaseModel):
    """One cell of the student x standard matrix. Only standards the student answered appear,
    so percent and level are never None here."""

    standard_code: str
    answer_count: int
    correct_count: int
    percent_correct: float
    level: MasteryLevel


class StudentMastery(BaseModel):
    student_id: uuid.UUID
    display_name: str
    standards: list[StudentStandardResult]  # empty if the student hasn't answered anything


class ClassroomStudentMastery(BaseModel):
    classroom_id: uuid.UUID
    classroom_name: str
    students: list[StudentMastery]


class RecentSubmission(BaseModel):
    student_id: uuid.UUID
    student_name: str
    ticket_id: uuid.UUID
    ticket_title: str
    score: Score
    submitted_at: datetime
