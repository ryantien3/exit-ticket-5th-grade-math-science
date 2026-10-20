"""student login codes and exit ticket releases

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-19

"""
import secrets
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# A frozen copy of app/auth.py's code format. Migrations don't import app code: the app
# keeps changing, but this file must keep doing exactly what it did on the day it was written.
LOGIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
LOGIN_CODE_LENGTH = 6


def upgrade() -> None:
    """Upgrade schema."""
    # Adding a required column to a table that already has rows takes three steps:
    # 1. add it as optional (existing rows get NULL),
    op.add_column("students", sa.Column("login_code", sa.Text(), nullable=True))

    # 2. give every existing student a random, unique code,
    connection = op.get_bind()
    student_ids = connection.scalars(sa.text("SELECT id FROM students")).all()
    codes: set[str] = set()
    while len(codes) < len(student_ids):
        codes.add("".join(secrets.choice(LOGIN_CODE_ALPHABET) for _ in range(LOGIN_CODE_LENGTH)))
    for student_id, code in zip(student_ids, codes):
        connection.execute(
            sa.text("UPDATE students SET login_code = :code WHERE id = :id"),
            {"code": code, "id": student_id},
        )

    # 3. then make it required, unique, and the right format.
    op.alter_column("students", "login_code", nullable=False)
    op.create_unique_constraint(op.f("uq_students_login_code"), "students", ["login_code"])
    op.create_check_constraint(
        op.f("ck_students_login_code_format"), "students", "login_code ~ '^[A-HJKMNP-Z2-9]{6}$'"
    )

    op.create_table(
        "classroom_exit_tickets",
        sa.Column("classroom_id", sa.Uuid(), nullable=False),
        sa.Column("ticket_id", sa.Uuid(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("classroom_id", "ticket_id", name=op.f("pk_classroom_exit_tickets")),
        sa.ForeignKeyConstraint(
            ["classroom_id"],
            ["classrooms.id"],
            name=op.f("fk_classroom_exit_tickets_classroom_id_classrooms"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ticket_id"],
            ["exit_tickets.id"],
            name=op.f("fk_classroom_exit_tickets_ticket_id_exit_tickets"),
            ondelete="CASCADE",
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("classroom_exit_tickets")
    op.drop_constraint(op.f("ck_students_login_code_format"), "students", type_="check")
    op.drop_constraint(op.f("uq_students_login_code"), "students", type_="unique")
    op.drop_column("students", "login_code")
