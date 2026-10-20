"""Endpoints about the signed-in student ("me")."""

from fastapi import APIRouter, Depends
from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.auth import require_student
from app.db import get_session
from app.models import Answer, ClassroomExitTicket, ExitTicket, Student
from app.routes.exit_tickets import question_count
from app.schemas import StudentExitTicket

router = APIRouter(tags=["me"])


@router.get("/me/exit-tickets", response_model=list[StudentExitTicket])
def list_my_exit_tickets(
    student: Student = Depends(require_student), session: Session = Depends(get_session)
) -> list[StudentExitTicket]:
    """Only tickets released to the student's classroom, newest release first."""
    submitted = (
        exists()
        .where(Answer.ticket_id == ExitTicket.id, Answer.student_id == student.id)
        .label("submitted")
    )
    rows = session.execute(
        select(ExitTicket.id, ExitTicket.title, ExitTicket.subject, question_count(), submitted)
        .join(ClassroomExitTicket, ClassroomExitTicket.ticket_id == ExitTicket.id)
        .where(ClassroomExitTicket.classroom_id == student.classroom_id)
        .order_by(ClassroomExitTicket.released_at.desc(), ExitTicket.title)
    )
    return [StudentExitTicket(**row._mapping) for row in rows]
