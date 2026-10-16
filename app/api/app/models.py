"""Database tables, as SQLAlchemy models.

The tables are created by Alembic migrations (alembic/versions/), never by
Base.metadata.create_all(). These classes describe the tables so Python code can
query them, and so "alembic check" can confirm the migrations match.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    MetaData,
    SmallInteger,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Gives every constraint a predictable name (e.g. fk_students_classroom_id_classrooms)
# instead of one Postgres invents. Migrations need the names to drop constraints later.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

SUBJECT_CHECK = "subject IN ('math', 'science')"


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def uuid_pk() -> Mapped[uuid.UUID]:
    # Python fills in the id when the ORM creates a row; the database default
    # covers rows inserted any other way (e.g. raw SQL).
    return mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )


def created_at() -> Mapped[datetime]:
    # timestamptz: Postgres stores the moment in UTC.
    return mapped_column(DateTime(timezone=True), server_default=func.now())


class Standard(Base):
    __tablename__ = "standards"
    __table_args__ = (
        CheckConstraint(SUBJECT_CHECK, name="subject_valid"),
        Index("ix_standards_domain", "domain"),
    )

    code: Mapped[str] = mapped_column(Text, primary_key=True)  # e.g. "NY-5.NF.4", "5-PS1-2"
    domain: Mapped[str] = mapped_column(Text)  # e.g. "NY-5.NF", "5-PS1"
    subject: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint(
            "CASE WHEN jsonb_typeof(choices) = 'array' "
            "THEN jsonb_array_length(choices) = 4 ELSE false END",
            name="choices_four",
        ),
        CheckConstraint("correct_choice IN ('A', 'B', 'C', 'D')", name="correct_choice_valid"),
        # Lets answers point at (question id, standard code) together, so an answer's
        # copied standard_code can never disagree with its question's.
        UniqueConstraint("id", "standard_code"),
        Index("ix_questions_standard_code", "standard_code"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    standard_code: Mapped[str] = mapped_column(Text, ForeignKey("standards.code"))
    prompt: Mapped[str] = mapped_column(Text)
    choices: Mapped[list[str]] = mapped_column(JSONB)  # 4 strings; index 0 is "A"
    correct_choice: Mapped[str] = mapped_column(Text)  # "A" to "D"
    explanation: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class ExitTicket(Base):
    __tablename__ = "exit_tickets"
    __table_args__ = (CheckConstraint(SUBJECT_CHECK, name="subject_valid"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    title: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at()


class ExitTicketQuestion(Base):
    __tablename__ = "exit_ticket_questions"
    __table_args__ = (
        UniqueConstraint("ticket_id", "position"),
        CheckConstraint("position >= 1", name="position_positive"),
        Index("ix_exit_ticket_questions_question_id", "question_id"),
    )

    ticket_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("exit_tickets.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("questions.id"), primary_key=True
    )
    position: Mapped[int] = mapped_column(SmallInteger)  # 1 = first question on the ticket


class Classroom(Base):
    __tablename__ = "classrooms"
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(Text)


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (UniqueConstraint("classroom_id", "display_name"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    classroom_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("classrooms.id"))
    display_name: Mapped[str] = mapped_column(Text)  # synthetic only, e.g. "Avery B."


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (
        # The question must be on this ticket.
        ForeignKeyConstraint(
            ["ticket_id", "question_id"],
            ["exit_ticket_questions.ticket_id", "exit_ticket_questions.question_id"],
        ),
        # The copied standard_code must match the question's real standard.
        ForeignKeyConstraint(
            ["question_id", "standard_code"],
            ["questions.id", "questions.standard_code"],
        ),
        CheckConstraint("chosen_choice IN ('A', 'B', 'C', 'D')", name="chosen_choice_valid"),
        # One answer per student per question per ticket (no retakes yet).
        UniqueConstraint("student_id", "ticket_id", "question_id"),
        Index("ix_answers_standard_code_answered_at", "standard_code", "answered_at"),
        Index("ix_answers_ticket_id", "ticket_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("students.id"))
    ticket_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    question_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    # Copied from the question so analytics can group by standard without joins.
    standard_code: Mapped[str] = mapped_column(Text)
    chosen_choice: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool]
    answered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
