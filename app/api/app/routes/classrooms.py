import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.auth import require_teacher
from app.db import get_session
from app.grading import mastery_level, percent
from app.models import Answer, Classroom, ClassroomExitTicket, ExitTicket, Standard, Student
from app.routes.exit_tickets import get_ticket_or_404, question_count
from app.schemas import (
    ClassroomExitTicketStatus,
    ClassroomMastery,
    ClassroomOut,
    ClassroomStudentMastery,
    RecentSubmission,
    Score,
    StandardMastery,
    StudentMastery,
    StudentOut,
    StudentStandardResult,
)

# Every endpoint in this file is for teachers only. Putting the check on the router
# means a new endpoint added here later can't forget it.
router = APIRouter(tags=["classrooms (teacher)"], dependencies=[Depends(require_teacher)])


def get_classroom_or_404(session: Session, classroom_id: uuid.UUID) -> Classroom:
    classroom = session.get(Classroom, classroom_id)
    if classroom is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Classroom not found: {classroom_id}")
    return classroom


def get_students(session: Session, classroom_id: uuid.UUID) -> list[Student]:
    return list(
        session.scalars(
            select(Student).where(Student.classroom_id == classroom_id).order_by(Student.display_name)
        )
    )


@router.get("/classrooms", response_model=list[ClassroomOut])
def list_classrooms(session: Session = Depends(get_session)) -> list[Classroom]:
    return list(session.scalars(select(Classroom).order_by(Classroom.name)))


@router.get("/classrooms/{classroom_id}/students", response_model=list[StudentOut])
def list_students(classroom_id: uuid.UUID, session: Session = Depends(get_session)) -> list[Student]:
    get_classroom_or_404(session, classroom_id)
    return get_students(session, classroom_id)


@router.get("/classrooms/{classroom_id}/mastery", response_model=ClassroomMastery)
def get_classroom_mastery(
    classroom_id: uuid.UUID, session: Session = Depends(get_session)
) -> ClassroomMastery:
    classroom = get_classroom_or_404(session, classroom_id)

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


@router.get("/classrooms/{classroom_id}/mastery/students", response_model=ClassroomStudentMastery)
def get_student_mastery(
    classroom_id: uuid.UUID, session: Session = Depends(get_session)
) -> ClassroomStudentMastery:
    classroom = get_classroom_or_404(session, classroom_id)

    # Same counting as the class view above, one level finer: per student, per standard.
    results: dict[uuid.UUID, list[StudentStandardResult]] = defaultdict(list)
    for student_id, standard_code, answer_count, correct_count in session.execute(
        select(
            Answer.student_id,
            Answer.standard_code,
            func.count(),
            func.count().filter(Answer.is_correct),
        )
        .join(Student, Student.id == Answer.student_id)
        .where(Student.classroom_id == classroom_id)
        .group_by(Answer.student_id, Answer.standard_code)
        .order_by(Answer.standard_code)
    ):
        percent_correct = percent(correct_count, answer_count)
        results[student_id].append(
            StudentStandardResult(
                standard_code=standard_code,
                answer_count=answer_count,
                correct_count=correct_count,
                percent_correct=percent_correct,
                level=mastery_level(percent_correct),
            )
        )

    # Built from the student list, not the answers, so students with no answers still appear.
    students = [
        StudentMastery(student_id=student.id, display_name=student.display_name, standards=results[student.id])
        for student in get_students(session, classroom_id)
    ]
    return ClassroomStudentMastery(classroom_id=classroom.id, classroom_name=classroom.name, students=students)


@router.get("/classrooms/{classroom_id}/recent-submissions", response_model=list[RecentSubmission])
def list_recent_submissions(
    classroom_id: uuid.UUID,
    limit: int = Query(10, ge=1, le=50),
    session: Session = Depends(get_session),
) -> list[RecentSubmission]:
    get_classroom_or_404(session, classroom_id)

    # There's no submissions table: a submission is all of one student's answers to one
    # ticket (the unique rule on answers allows only one per student per ticket).
    submitted_at = func.max(Answer.answered_at).label("submitted_at")
    rows = session.execute(
        select(
            Student.id,
            Student.display_name,
            ExitTicket.id,
            ExitTicket.title,
            func.count(),
            func.count().filter(Answer.is_correct),
            submitted_at,
        )
        .join(Student, Student.id == Answer.student_id)
        .join(ExitTicket, ExitTicket.id == Answer.ticket_id)
        .where(Student.classroom_id == classroom_id)
        .group_by(Student.id, ExitTicket.id)
        .order_by(submitted_at.desc(), Student.display_name)
        .limit(limit)
    )
    return [
        RecentSubmission(
            student_id=student_id,
            student_name=student_name,
            ticket_id=ticket_id,
            ticket_title=ticket_title,
            score=Score(correct=correct, total=total, percent=percent(correct, total)),
            submitted_at=when,
        )
        for student_id, student_name, ticket_id, ticket_title, total, correct, when in rows
    ]


# --- Releasing exit tickets ---


def ticket_statuses(session: Session, classroom_id: uuid.UUID, ticket_id: uuid.UUID | None = None):
    """Every exit ticket (or just one) with whether it's released to this classroom."""
    query = (
        select(
            ExitTicket.id,
            ExitTicket.title,
            ExitTicket.subject,
            question_count(),
            (ClassroomExitTicket.ticket_id.is_not(None)).label("released"),
            ClassroomExitTicket.released_at,
        )
        # The classroom condition goes in the ON clause, not WHERE: tickets with no
        # release row for this class must still come back (with released_at NULL).
        .outerjoin(
            ClassroomExitTicket,
            and_(
                ClassroomExitTicket.ticket_id == ExitTicket.id,
                ClassroomExitTicket.classroom_id == classroom_id,
            ),
        )
        .order_by(ExitTicket.title)
    )
    if ticket_id is not None:
        query = query.where(ExitTicket.id == ticket_id)
    return [ClassroomExitTicketStatus(**row._mapping) for row in session.execute(query)]


@router.get("/classrooms/{classroom_id}/exit-tickets", response_model=list[ClassroomExitTicketStatus])
def list_classroom_exit_tickets(
    classroom_id: uuid.UUID, session: Session = Depends(get_session)
) -> list[ClassroomExitTicketStatus]:
    get_classroom_or_404(session, classroom_id)
    return ticket_statuses(session, classroom_id)


# PUT and DELETE are idempotent: doing either twice leaves things the same as doing it once,
# so a double-click or a retry after a network blip is harmless.
@router.put(
    "/classrooms/{classroom_id}/exit-tickets/{ticket_id}/release",
    response_model=ClassroomExitTicketStatus,
)
def release_exit_ticket(
    classroom_id: uuid.UUID, ticket_id: uuid.UUID, session: Session = Depends(get_session)
) -> ClassroomExitTicketStatus:
    get_classroom_or_404(session, classroom_id)
    get_ticket_or_404(session, ticket_id)
    # Already released? Keep the original released_at instead of failing or resetting it.
    session.execute(
        insert(ClassroomExitTicket)
        .values(classroom_id=classroom_id, ticket_id=ticket_id)
        .on_conflict_do_nothing()
    )
    session.commit()
    return ticket_statuses(session, classroom_id, ticket_id)[0]


@router.delete(
    "/classrooms/{classroom_id}/exit-tickets/{ticket_id}/release",
    status_code=status.HTTP_204_NO_CONTENT,
)
def hide_exit_ticket(classroom_id: uuid.UUID, ticket_id: uuid.UUID, session: Session = Depends(get_session)) -> Response:
    """Hide it again. Answers already submitted are kept; new submissions are refused."""
    get_classroom_or_404(session, classroom_id)
    get_ticket_or_404(session, ticket_id)
    session.execute(
        delete(ClassroomExitTicket).where(
            ClassroomExitTicket.classroom_id == classroom_id, ClassroomExitTicket.ticket_id == ticket_id
        )
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
