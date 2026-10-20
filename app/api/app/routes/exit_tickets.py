import logging
import uuid
from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import events
from app.auth import get_student_or_teacher, require_student, require_teacher
from app.db import get_session
from app.grading import percent, summarize_by_standard
from app.models import Answer, ClassroomExitTicket, ExitTicket, ExitTicketQuestion, Question, Student
from app.schemas import (
    LETTERS,
    Choice,
    ExitTicketDetail,
    ExitTicketSummary,
    QuestionResult,
    Score,
    StudentQuestion,
    SubmissionIn,
    SubmissionOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["exit tickets"])


def ticket_not_found(ticket_id: uuid.UUID) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, f"Exit ticket not found: {ticket_id}")


def get_ticket_or_404(session: Session, ticket_id: uuid.UUID) -> ExitTicket:
    ticket = session.get(ExitTicket, ticket_id)
    if ticket is None:
        raise ticket_not_found(ticket_id)
    return ticket


def get_open_ticket_or_404(session: Session, ticket_id: uuid.UUID, student: Student | None) -> ExitTicket:
    """The ticket, if this viewer may see it: teachers (student=None) see every ticket;
    students only see tickets released to their classroom. A hidden ticket gets the same
    404 as one that doesn't exist, so students can't find out which tickets are coming."""
    ticket = get_ticket_or_404(session, ticket_id)
    if student is not None and session.get(ClassroomExitTicket, (student.classroom_id, ticket_id)) is None:
        raise ticket_not_found(ticket_id)
    return ticket


def question_count():
    """A "count this ticket's questions" subquery, for use as a column next to ExitTicket."""
    return (
        select(func.count())
        .where(ExitTicketQuestion.ticket_id == ExitTicket.id)
        .scalar_subquery()
        .label("question_count")
    )


def get_ticket_questions(session: Session, ticket_id: uuid.UUID) -> list[tuple[int, Question]]:
    """(position, question) pairs in the order students see them."""
    rows = session.execute(
        select(ExitTicketQuestion.position, Question)
        .join(Question, Question.id == ExitTicketQuestion.question_id)
        .where(ExitTicketQuestion.ticket_id == ticket_id)
        .order_by(ExitTicketQuestion.position)
    )
    return [(position, question) for position, question in rows]


def unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)


def id_list(ids) -> str:
    return ", ".join(str(i) for i in ids)


@router.get(
    "/exit-tickets",
    response_model=list[ExitTicketSummary],
    dependencies=[Depends(require_teacher)],  # students use /me/exit-tickets
)
def list_exit_tickets(session: Session = Depends(get_session)) -> list[ExitTicketSummary]:
    rows = session.execute(
        select(ExitTicket.id, ExitTicket.title, ExitTicket.subject, question_count()).order_by(ExitTicket.title)
    )
    return [ExitTicketSummary(**row._mapping) for row in rows]


@router.get("/exit-tickets/{ticket_id}", response_model=ExitTicketDetail)
def get_exit_ticket(
    ticket_id: uuid.UUID,
    student: Student | None = Depends(get_student_or_teacher),
    session: Session = Depends(get_session),
) -> ExitTicketDetail:
    ticket = get_open_ticket_or_404(session, ticket_id, student)
    # Built from StudentQuestion, which has no correct answer or explanation fields:
    # anything sent here is visible in the browser's network tab.
    questions = [
        StudentQuestion(
            id=question.id,
            position=position,
            standard_code=question.standard_code,
            prompt=question.prompt,
            choices=[Choice(letter=LETTERS[i], text=text) for i, text in enumerate(question.choices)],
        )
        for position, question in get_ticket_questions(session, ticket_id)
    ]
    return ExitTicketDetail(id=ticket.id, title=ticket.title, subject=ticket.subject, questions=questions)


@router.post(
    "/exit-tickets/{ticket_id}/submissions",
    response_model=SubmissionOut,
    status_code=status.HTTP_201_CREATED,
)
def submit_exit_ticket(
    ticket_id: uuid.UUID,
    submission: SubmissionIn,
    student: Student = Depends(require_student),
    session: Session = Depends(get_session),
) -> SubmissionOut:
    # Also rejects a ticket the teacher hid again after the student opened it.
    get_open_ticket_or_404(session, ticket_id, student)
    ticket_questions = get_ticket_questions(session, ticket_id)
    questions = {question.id: question for _, question in ticket_questions}

    # --- Validate the whole submission before saving anything ---
    submitted_ids = [answer.question_id for answer in submission.answers]
    duplicates = [qid for qid, count in Counter(submitted_ids).items() if count > 1]
    if duplicates:
        raise unprocessable(f"Questions answered more than once: {id_list(duplicates)}")
    not_on_ticket = [qid for qid in submitted_ids if qid not in questions]
    if not_on_ticket:
        raise unprocessable(f"Questions not on this exit ticket: {id_list(not_on_ticket)}")
    missing = [qid for qid in questions if qid not in set(submitted_ids)]
    if missing:
        raise unprocessable(f"Every question must be answered. Missing: {id_list(missing)}")

    already_submitted = session.scalar(
        select(func.count())
        .select_from(Answer)
        .where(Answer.student_id == student.id, Answer.ticket_id == ticket_id)
    )
    if already_submitted:
        raise HTTPException(status.HTTP_409_CONFLICT, "This student already submitted this exit ticket.")

    # --- Grade on the server and save one row per question ---
    answered_at = datetime.now(timezone.utc)
    chosen = {answer.question_id: answer.chosen_choice for answer in submission.answers}
    rows = [
        Answer(
            id=uuid.uuid4(),
            student_id=student.id,
            ticket_id=ticket_id,
            question_id=question.id,
            standard_code=question.standard_code,  # copied for join-free analytics
            chosen_choice=chosen[question.id],
            is_correct=chosen[question.id] == question.correct_choice,
            answered_at=answered_at,
        )
        for _, question in ticket_questions
    ]
    # Plain values captured now, because the ORM objects expire after commit.
    answer_events = [
        events.AnswerEvent(
            answer_id=row.id,
            student_id=row.student_id,
            classroom_id=student.classroom_id,
            ticket_id=row.ticket_id,
            question_id=row.question_id,
            standard_code=row.standard_code,
            chosen_choice=row.chosen_choice,
            is_correct=row.is_correct,
            answered_at=row.answered_at,
        )
        for row in rows
    ]
    results = [
        QuestionResult(
            question_id=question.id,
            chosen_choice=chosen[question.id],
            correct_choice=question.correct_choice,
            is_correct=chosen[question.id] == question.correct_choice,
            explanation=question.explanation,
        )
        for _, question in ticket_questions
    ]

    session.add_all(rows)
    try:
        session.commit()
    except IntegrityError:
        # Two identical submissions raced past the check above; the unique rule caught it.
        session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "This student already submitted this exit ticket.")

    # Publish only after the commit, so analytics never sees answers that weren't saved.
    # A failed publish is logged, not returned as an error: the answers are already saved.
    for event in answer_events:
        try:
            events.publish_answer_event(event)
        except Exception:
            logger.exception("Failed to publish answer event %s", event.answer_id)

    correct = sum(result.is_correct for result in results)
    return SubmissionOut(
        score=Score(correct=correct, total=len(results), percent=percent(correct, len(results))),
        results=results,
        standards=summarize_by_standard(
            (question.standard_code, result.is_correct)
            for (_, question), result in zip(ticket_questions, results)
        ),
    )
