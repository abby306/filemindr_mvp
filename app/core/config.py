"""Application settings, loaded from the environment / `.env`.

A single `Settings` instance is the source of truth for connection strings,
provider keys, and paths. Secrets live only in `.env` (git-ignored) — never
hardcode them here.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Project root = two levels up from this file (app/core/config.py -> project/).
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Typed view of the process environment.

    Field names map to upper-case env vars (`database_url` <- `DATABASE_URL`),
    matching the keys in `.env.example`.
    """

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Datastores
    database_url: str = "postgresql+psycopg://filemindr:localdev@localhost:5432/filemindr"
    redis_url: str = "redis://localhost:6379/0"

    # External providers (blank until set in .env)
    openai_api_key: str = ""
    deepseek_api_key: str = ""
    google_application_credentials: str = "./secrets/vision-credentials.json"

    # Local filesystem + runtime
    storage_dir: str = "./storage"
    app_env: str = "development"

    @property
    def storage_path(self) -> Path:
        """`storage_dir` resolved to an absolute path under the project root."""
        path = Path(self.storage_dir)
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide cached `Settings` instance."""
    return Settings()
