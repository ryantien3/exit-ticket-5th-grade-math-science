import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.grading import mastery_level, percent
from app.models import Answer, Classroom, Standard, Student
from app.schemas import ClassroomMastery, StandardMastery

router = APIRouter(tags=["classrooms"])


@router.get("/classrooms/{classroom_id}/mastery", response_model=ClassroomMastery)
def get_classroom_mastery(
    classroom_id: uuid.UUID, session: Session = Depends(get_session)
) -> ClassroomMastery:
    classroom = session.get(Classroom, classroom_id)
    if classroom is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Classroom not found: {classroom_id}")

    # standard_code lives on each answer, so grouping by standard needs no join to questions.
    counts = {
        standard_code: (answer_count, correct_count)
        for standard_code, answer_count, correct_count in session.execute(
            select(
                Answer.standard_code,
                func.count(),
                func.count().filter(Answer.is_correct),
            )
            .join(Student, Student.id == Answer.student_id)
            .where(Student.classroom_id == classroom_id)
            .group_by(Answer.standard_code)
        )
    }

    # Every standard is listed, so teachers also see the ones with no answers yet.
    standards = []
    for standard in session.scalars(select(Standard).order_by(Standard.code)):
        answer_count, correct_count = counts.get(standard.code, (0, 0))
        percent_correct = percent(correct_count, answer_count)
        standards.append(
            StandardMastery(
                standard_code=standard.code,
                subject=standard.subject,
                description=standard.description,
                answer_count=answer_count,
                correct_count=correct_count,
                percent_correct=percent_correct,
                level=mastery_level(percent_correct),
            )
        )
    return ClassroomMastery(classroom_id=classroom.id, classroom_name=classroom.name, standards=standards)
