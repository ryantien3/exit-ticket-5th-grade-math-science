"""create core tables

Revision ID: 0001
Revises:
Create Date: 2026-10-15

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "standards",
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("domain", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("code", name=op.f("pk_standards")),
        sa.CheckConstraint("subject IN ('math', 'science')", name=op.f("ck_standards_subject_valid")),
    )
    op.create_index(op.f("ix_standards_domain"), "standards", ["domain"])

    op.create_table(
        "questions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("standard_code", sa.Text(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("choices", postgresql.JSONB(), nullable=False),
        sa.Column("correct_choice", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_questions")),
        sa.ForeignKeyConstraint(
            ["standard_code"], ["standards.code"], name=op.f("fk_questions_standard_code_standards")
        ),
        sa.UniqueConstraint("id", "standard_code", name=op.f("uq_questions_id_standard_code")),
        sa.CheckConstraint(
            "CASE WHEN jsonb_typeof(choices) = 'array' "
            "THEN jsonb_array_length(choices) = 4 ELSE false END",
            name=op.f("ck_questions_choices_four"),
        ),
        sa.CheckConstraint(
            "correct_choice IN ('A', 'B', 'C', 'D')", name=op.f("ck_questions_correct_choice_valid")
        ),
    )
    op.create_index(op.f("ix_questions_standard_code"), "questions", ["standard_code"])

    op.create_table(
        "exit_tickets",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exit_tickets")),
        sa.CheckConstraint(
            "subject IN ('math', 'science')", name=op.f("ck_exit_tickets_subject_valid")
        ),
    )

    op.create_table(
        "exit_ticket_questions",
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.SmallInteger(), nullable=False),
        sa.PrimaryKeyConstraint("ticket_id", "question_id", name=op.f("pk_exit_ticket_questions")),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["exit_tickets.id"],
            name=op.f("fk_exit_ticket_questions_ticket_id_exit_tickets"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["questions.id"],
            name=op.f("fk_exit_ticket_questions_question_id_questions"),
        ),
        sa.UniqueConstraint(
            "ticket_id", "position", name=op.f("uq_exit_ticket_questions_ticket_id_position")
        ),
        sa.CheckConstraint("position >= 1", name=op.f("ck_exit_ticket_questions_position_positive")),
    )
    op.create_index(
        op.f("ix_exit_ticket_questions_question_id"), "exit_ticket_questions", ["question_id"]
    )

    op.create_table(
        "classrooms",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_classrooms")),
        sa.UniqueConstraint("name", name=op.f("uq_classrooms_name")),
    )

    op.create_table(
        "students",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("classroom_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_students")),
        sa.ForeignKeyConstraint(
            ["classroom_id"], ["classrooms.id"], name=op.f("fk_students_classroom_id_classrooms")
        ),
        sa.UniqueConstraint(
            "classroom_id", "display_name", name=op.f("uq_students_classroom_id_display_name")
        ),
    )

    op.create_table(
        "answers",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Uuid(), nullable=False),
        sa.Column("standard_code", sa.Text(), nullable=False),
        sa.Column("chosen_choice", sa.Text(), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column(
            "answered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_answers")),
        sa.ForeignKeyConstraint(
            ["student_id"], ["students.id"], name=op.f("fk_answers_student_id_students")
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id", "question_id"],
            ["exit_ticket_questions.ticket_id", "exit_ticket_questions.question_id"],
            name=op.f("fk_answers_ticket_id_question_id_exit_ticket_questions"),
        ),
        sa.ForeignKeyConstraint(
            ["question_id", "standard_code"],
            ["questions.id", "questions.standard_code"],
            name=op.f("fk_answers_question_id_standard_code_questions"),
        ),
        sa.UniqueConstraint(
            "student_id",
            "ticket_id",
            "question_id",
            name=op.f("uq_answers_student_id_ticket_id_question_id"),
        ),
        sa.CheckConstraint(
            "chosen_choice IN ('A', 'B', 'C', 'D')", name=op.f("ck_answers_chosen_choice_valid")
        ),
    )
    op.create_index(
        op.f("ix_answers_standard_code_answered_at"), "answers", ["standard_code", "answered_at"]
    )
    op.create_index(op.f("ix_answers_ticket_id"), "answers", ["ticket_id"])


def downgrade() -> None:
    """Downgrade schema."""
    # Reverse order: a table can only be dropped once nothing points at it.
    op.drop_table("answers")
    op.drop_table("students")
    op.drop_table("classrooms")
    op.drop_table("exit_ticket_questions")
    op.drop_table("exit_tickets")
    op.drop_table("questions")
    op.drop_table("standards")
