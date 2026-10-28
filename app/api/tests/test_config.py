"""Settings sources: environment variables, .env, and secret files mounted on GKE."""

from pathlib import Path

import pytest

from app.config import Settings, existing_dir

FILE_SESSION_SECRET = "secret-from-a-mounted-file-0123456789abcdef"
FILE_TEACHER_PASSCODE = "passcode-from-a-file"


@pytest.fixture
def secrets_dir(tmp_path: Path) -> Path:
    """A folder laid out like the Secret Manager add-on's mount: one file per secret."""
    # Trailing newline on one of them: a value saved with one must still work.
    (tmp_path / "session_secret").write_text(FILE_SESSION_SECRET + "\n")
    (tmp_path / "teacher_passcode").write_text(FILE_TEACHER_PASSCODE)
    return tmp_path


@pytest.fixture
def no_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # conftest.py sets test values for these; remove them, as on GKE.
    monkeypatch.delenv("SESSION_SECRET")
    monkeypatch.delenv("TEACHER_PASSCODE")


def test_secrets_are_read_from_files(secrets_dir: Path, no_secret_env: None) -> None:
    settings = Settings(_env_file=None, _secrets_dir=secrets_dir)

    assert settings.session_secret.get_secret_value() == FILE_SESSION_SECRET
    assert settings.teacher_passcode.get_secret_value() == FILE_TEACHER_PASSCODE


def test_environment_variables_win_over_files(
    secrets_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEACHER_PASSCODE", "passcode-from-the-environment")

    settings = Settings(_env_file=None, _secrets_dir=secrets_dir)

    assert settings.teacher_passcode.get_secret_value() == "passcode-from-the-environment"


def test_secret_files_are_still_validated(
    secrets_dir: Path, no_secret_env: None
) -> None:
    (secrets_dir / "session_secret").write_text("too-short")

    with pytest.raises(ValueError, match="session_secret"):
        Settings(_env_file=None, _secrets_dir=secrets_dir)


def test_missing_secrets_dir_is_ignored(tmp_path: Path) -> None:
    assert existing_dir(tmp_path) == tmp_path
    assert existing_dir(tmp_path / "not-mounted") is None
