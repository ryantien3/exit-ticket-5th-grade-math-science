"""Look up seeded ids and build submissions, using the same keys as scripts/seed_data.py."""

from app.schemas import LETTERS
from seed import stable_id
from seed_data import CLASSROOM_NAME, EXIT_TICKETS, QUESTIONS, student_display_names

QUESTIONS_BY_KEY = {q["key"]: q for q in QUESTIONS}
TICKETS_BY_KEY = {t["key"]: t for t in EXIT_TICKETS}
STUDENT_NAMES = student_display_names()
CLASSROOM_ID = stable_id("classroom", CLASSROOM_NAME)


def ticket_id(key: str) -> str:
    return str(stable_id("ticket", key))


def question_id(key: str) -> str:
    return str(stable_id("question", key))


def student_id(index: int) -> str:
    return str(stable_id("student", f"{CLASSROOM_NAME}:{STUDENT_NAMES[index]}"))


def wrong_letter(correct: str) -> str:
    return LETTERS[(LETTERS.index(correct) + 1) % 4]


def answers_for(ticket_key: str, wrong: tuple[str, ...] = ()) -> list[dict]:
    """Answers for every question on a ticket: correct, except question keys in `wrong`."""
    answers = []
    for key in TICKETS_BY_KEY[ticket_key]["questions"]:
        correct = QUESTIONS_BY_KEY[key]["correct"]
        chosen = wrong_letter(correct) if key in wrong else correct
        answers.append({"question_id": question_id(key), "chosen_choice": chosen})
    return answers
