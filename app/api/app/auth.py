"""Login codes, session tokens, and the FastAPI dependencies that check them.

How sign-in works:
1. A student types their login code (or a teacher their passcode) into POST /student-sessions
   (or /teacher-sessions).
2. The API answers with a token: a JWT signed with SESSION_SECRET that says who you are,
   your role, and when the token expires. The signature means it can't be edited or forged
   without the secret.
3. The browser sends the token on every request in the header "Authorization: Bearer <token>".
   The dependencies below check it and either return who's asking or answer 401/403.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import Student

# No 0/O, 1/I/L: easy to tell apart when a 10-year-old copies the code from a card.
LOGIN_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # 31 characters
LOGIN_CODE_LENGTH = 6  # 31^6 = about 887 million possible codes

TOKEN_ALGORITHM = "HS256"
STUDENT_TOKEN_LIFETIME = timedelta(hours=2)
TEACHER_TOKEN_LIFETIME = timedelta(hours=8)

Role = Literal["student", "teacher"]


def new_login_code() -> str:
    # secrets (not random) uses the operating system's cryptographic randomness,
    # so codes can't be predicted from earlier ones.
    return "".join(secrets.choice(LOGIN_CODE_ALPHABET) for _ in range(LOGIN_CODE_LENGTH))


def normalize_login_code(typed: str) -> str:
    """Accept "k7p-4qx" or " K7P 4QX " as K7P4QX."""
    return "".join(typed.split()).replace("-", "").upper()


def passcode_matches(typed: str) -> bool:
    expected = get_settings().teacher_passcode.get_secret_value()
    # compare_digest takes the same time whether the first or last character is wrong,
    # so response times can't be used to guess the passcode one character at a time.
    return secrets.compare_digest(typed.encode(), expected.encode())


@dataclass(frozen=True)
class IssuedToken:
    token: str
    expires_at: datetime


def create_token(role: Role, subject: str, lifetime: timedelta) -> IssuedToken:
    now = datetime.now(timezone.utc)
    expires_at = now + lifetime
    claims = {"sub": subject, "role": role, "iat": now, "exp": expires_at}
    secret = get_settings().session_secret.get_secret_value()
    return IssuedToken(jwt.encode(claims, secret, algorithm=TOKEN_ALGORITHM), expires_at)


def create_student_token(student_id: uuid.UUID) -> IssuedToken:
    return create_token("student", str(student_id), STUDENT_TOKEN_LIFETIME)


def create_teacher_token() -> IssuedToken:
    return create_token("teacher", "teacher", TEACHER_TOKEN_LIFETIME)


# auto_error=False: we raise our own 401 (with a clear message) when the header is missing.
# Declaring it also adds the "Authorize" button to /docs.
bearer_scheme = HTTPBearer(auto_error=False)


def unauthorized(message: str) -> HTTPException:
    # The WWW-Authenticate header tells clients which kind of credentials to send.
    return HTTPException(status.HTTP_401_UNAUTHORIZED, message, headers={"WWW-Authenticate": "Bearer"})


def get_claims(credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme)) -> dict:
    """The verified contents of the request's token. 401 if it's missing, forged or expired."""
    if credentials is None:
        raise unauthorized("Sign in first.")
    try:
        return jwt.decode(
            credentials.credentials,
            get_settings().session_secret.get_secret_value(),
            # Only accept our algorithm, so a token can't pick a weaker one (e.g. "none").
            algorithms=[TOKEN_ALGORITHM],
            options={"require": ["sub", "role", "exp"]},
        )
    except jwt.InvalidTokenError:
        # Covers a bad signature, an expired token, and anything that isn't a JWT at all.
        raise unauthorized("Your sign-in has expired or isn't valid. Sign in again.")


def require_teacher(claims: dict = Depends(get_claims)) -> None:
    if claims["role"] != "teacher":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only teachers can do this.")


def require_student(claims: dict = Depends(get_claims), session: Session = Depends(get_session)) -> Student:
    if claims["role"] != "student":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only students can do this.")
    student = session.get(Student, uuid.UUID(claims["sub"]))
    if student is None:  # e.g. the student was removed after signing in
        raise unauthorized("Sign in again.")
    return student


def get_student_or_teacher(
    claims: dict = Depends(get_claims), session: Session = Depends(get_session)
) -> Student | None:
    """The signed-in student, or None for a teacher."""
    if claims["role"] == "teacher":
        return None
    return require_student(claims, session)
