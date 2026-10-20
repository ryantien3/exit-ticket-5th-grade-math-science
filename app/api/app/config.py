"""Application settings, read from environment variables.

Real environment variables always win. A local app/api/.env file is only a fallback
for development; in GKE there is no .env file.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# app/api/.env, found the same way no matter which directory a command runs from.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # No default: the app refuses to start if DATABASE_URL is missing.
    database_url: str
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    environment: str = "local"

    # Secrets: no defaults, so a missing one stops the app instead of running insecurely.
    # SecretStr hides the value in logs and error messages ("**********").
    # Signs login tokens. Anyone with this key can forge a login, so it must be long and random.
    session_secret: SecretStr = Field(min_length=32)
    # Shared teacher passcode, until teacher sign-in moves to Google (IAP) in Phase 3.
    teacher_passcode: SecretStr = Field(min_length=8)

    @field_validator("session_secret", "teacher_passcode")
    @classmethod
    def not_the_example_value(cls, value: SecretStr) -> SecretStr:
        # .env.example is public, so its placeholders must never work as real secrets.
        if value.get_secret_value().startswith("replace-me"):
            raise ValueError("still set to the placeholder from .env.example; choose a real value")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
