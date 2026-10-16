"""Application settings, read from environment variables.

Real environment variables always win. A local app/api/.env file is only a fallback
for development; in GKE there is no .env file.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

# app/api/.env, found the same way no matter which directory a command runs from.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    # No default: the app refuses to start if DATABASE_URL is missing.
    database_url: str
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    environment: str = "local"


@lru_cache
def get_settings() -> Settings:
    return Settings()
