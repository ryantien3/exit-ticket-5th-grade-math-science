"""Sign-in: trade a login code (students) or passcode (teachers) for a session token."""

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    create_student_token,
    create_teacher_token,
    normalize_login_code,
    passcode_matches,
    unauthorized,
)
from app.db import get_session
from app.models import Classroom, Student
from app.schemas import StudentLoginIn, StudentSessionOut, TeacherLoginIn, TeacherSessionOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["sign-in"])


# 201 Created: each successful sign-in creates a new session.
@router.post("/student-sessions", response_model=StudentSessionOut, status_code=status.HTTP_201_CREATED)
def student_sign_in(login: StudentLoginIn, session: Session = Depends(get_session)) -> StudentSessionOut:
    row = session.execute(
        select(Student, Classroom.name)
        .join(Classroom, Classroom.id == Student.classroom_id)
        .where(Student.login_code == normalize_login_code(login.login_code))
    ).first()
    if row is None:
        # Same answer for every wrong code, so it never hints at which codes exist.
        logger.warning("Failed student sign-in")
        raise unauthorized("That code didn't work.")
    student, classroom_name = row
    issued = create_student_token(student.id)
    return StudentSessionOut(
        token=issued.token,
        expires_at=issued.expires_at,
        display_name=student.display_name,
        classroom_name=classroom_name,
    )


@router.post("/teacher-sessions", response_model=TeacherSessionOut, status_code=status.HTTP_201_CREATED)
def teacher_sign_in(login: TeacherLoginIn) -> TeacherSessionOut:
    if not passcode_matches(login.passcode):
        logger.warning("Failed teacher sign-in")
        raise unauthorized("That passcode didn't work.")
    issued = create_teacher_token()
    return TeacherSessionOut(token=issued.token, expires_at=issued.expires_at)
