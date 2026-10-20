"""Sign-in, tokens, and who is allowed to call what."""

import uuid
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from sqlalchemy import select

from app.auth import TOKEN_ALGORITHM, create_token
from app.config import get_settings
from app.models import Student
from seed_helpers import CLASSROOM_ID, STUDENT_NAMES, bearer, student_headers, student_id, teacher_headers, ticket_id


def login_code(db_session, index=0):
    return db_session.scalar(select(Student.login_code).where(Student.id == uuid.UUID(student_id(index))))


# --- Signing in ---


def test_student_sign_in(client, db_session):
    response = client.post("/student-sessions", json={"login_code": login_code(db_session)})
    assert response.status_code == 201
    body = response.json()
    assert body["display_name"] == STUDENT_NAMES[0]
    assert body["classroom_name"] == "Class 5-201"
    # The token works for student endpoints.
    assert client.get("/me/exit-tickets", headers=bearer(body["token"])).status_code == 200


def test_login_code_ignores_case_spaces_and_dashes(client, db_session):
    code = login_code(db_session)
    typed = f" {code[:3].lower()}-{code[3:].lower()} "
    assert client.post("/student-sessions", json={"login_code": typed}).status_code == 201


def test_wrong_login_code_is_401(client):
    response = client.post("/student-sessions", json={"login_code": "ZZZZZZ"})
    assert response.status_code == 401
    assert response.json()["detail"] == "That code didn't work."
    assert response.headers["www-authenticate"] == "Bearer"


def test_teacher_sign_in(client):
    response = client.post("/teacher-sessions", json={"passcode": "test-teacher-passcode"})
    assert response.status_code == 201
    token = response.json()["token"]
    assert client.get("/classrooms", headers=bearer(token)).status_code == 200


def test_wrong_teacher_passcode_is_401(client):
    response = client.post("/teacher-sessions", json={"passcode": "nope"})
    assert response.status_code == 401


# --- Who can call what ---

TEACHER_ONLY = [
    ("GET", "/exit-tickets"),
    ("GET", "/classrooms"),
    ("GET", f"/classrooms/{CLASSROOM_ID}/students"),
    ("GET", f"/classrooms/{CLASSROOM_ID}/mastery"),
    ("GET", f"/classrooms/{CLASSROOM_ID}/mastery/students"),
    ("GET", f"/classrooms/{CLASSROOM_ID}/recent-submissions"),
    ("GET", f"/classrooms/{CLASSROOM_ID}/exit-tickets"),
    ("PUT", f"/classrooms/{CLASSROOM_ID}/exit-tickets/{ticket_id('fractions-multiply')}/release"),
    ("DELETE", f"/classrooms/{CLASSROOM_ID}/exit-tickets/{ticket_id('fractions-multiply')}/release"),
]


@pytest.mark.parametrize(("method", "path"), TEACHER_ONLY)
def test_teacher_only_endpoints(client, method, path):
    assert client.request(method, path).status_code == 401  # not signed in
    assert client.request(method, path, headers=student_headers(0)).status_code == 403  # wrong role


@pytest.mark.parametrize("path", ["/me/exit-tickets", f"/exit-tickets/{ticket_id('fractions-multiply')}"])
def test_student_endpoints_need_sign_in(client, path):
    assert client.get(path).status_code == 401


def test_teachers_have_no_me_endpoint(client):
    assert client.get("/me/exit-tickets", headers=teacher_headers()).status_code == 403


def test_public_endpoints_need_no_sign_in(client):
    assert client.get("/standards").status_code == 200
    assert client.get("/healthz").status_code == 200


# --- Bad tokens ---


def test_expired_token_is_401(client):
    expired = create_token("teacher", "teacher", timedelta(seconds=-1)).token
    assert client.get("/classrooms", headers=bearer(expired)).status_code == 401


def test_token_signed_with_another_secret_is_401(client):
    forged = jwt.encode(
        {"sub": "teacher", "role": "teacher", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        "a-different-secret-that-is-at-least-32-bytes-long",
        algorithm=TOKEN_ALGORITHM,
    )
    assert client.get("/classrooms", headers=bearer(forged)).status_code == 401


def test_edited_token_is_401(client):
    # A student changes their token's role to "teacher": the signature no longer matches.
    token = student_headers(0)["Authorization"].removeprefix("Bearer ")
    claims = jwt.decode(token, options={"verify_signature": False})
    header, _, signature = token.split(".")
    tampered_payload = jwt.encode({**claims, "role": "teacher"}, "x" * 32, algorithm=TOKEN_ALGORITHM).split(".")[1]
    tampered = f"{header}.{tampered_payload}.{signature}"
    assert client.get("/classrooms", headers=bearer(tampered)).status_code == 401


def test_unsigned_token_is_401(client):
    # alg "none" means "no signature": must never be accepted.
    unsigned = jwt.encode(
        {"sub": "teacher", "role": "teacher", "exp": datetime.now(timezone.utc) + timedelta(hours=1)},
        None,
        algorithm="none",
    )
    assert client.get("/classrooms", headers=bearer(unsigned)).status_code == 401


def test_garbage_token_is_401(client):
    assert client.get("/classrooms", headers=bearer("not-a-token")).status_code == 401


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SESSION_SECRET", "too-short"),
        ("SESSION_SECRET", "replace-me-with-the-output-of-the-command-above"),
        ("TEACHER_PASSCODE", "replace-me"),
        ("TEACHER_PASSCODE", "short"),
    ],
)
def test_settings_reject_weak_or_placeholder_secrets(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    try:
        with pytest.raises(ValueError):
            get_settings()
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
