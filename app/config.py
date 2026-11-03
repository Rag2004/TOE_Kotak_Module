"""TOE Kotak configuration. Loads only this module's .env."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_SERVICE_ROOT = Path(__file__).resolve().parents[1]
_env_override = os.environ.get("ENV_FILE")
_ENV_FILE = Path(_env_override) if _env_override else (_SERVICE_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE) if _ENV_FILE.exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    REDIS_URL: str = "redis://localhost:6379/0"
    MONGO_URI: str = "mongodb://localhost:27017"
    MONGO_DB_NAME: str = "quant_db"
    LOG_LEVEL: str = "INFO"

    STREAM_ORDER_INTENTS: str = "stream:order_intents_kotak_sandbox"
    STREAM_EXECUTIONS: str = "stream:executions"

    TOE_CONSUMER_GROUP: str = "toe_kotak_group"
    TOE_CONSUMER_NAME: str = "toe_kotak_worker_1"
    TOE_KOTAK_PORT: int = 8005

    NEO_ENVIRONMENT: str = Field(
        default="prod",
        validation_alias=AliasChoices("NEO_ENVIRONMENT", "TOE_KOTAK_ENVIRONMENT"),
    )
    NEO_CONSUMER_KEY: str = Field(
        default="",
        validation_alias=AliasChoices(
            "NEO_ACCESS_TOKEN",
            "NEO_CONSUMER_KEY",
            "TOE_KOTAK_ACCESS_TOKEN",
            "TOE_KOTAK_CONSUMER_KEY",
        ),
    )
    NEO_MOBILE_NUMBER: str = Field(
        default="",
        validation_alias=AliasChoices("NEO_MOBILE_NUMBER", "TOE_KOTAK_MOBILE_NUMBER"),
    )
    NEO_UCC: str = Field(
        default="",
        validation_alias=AliasChoices(
            "NEO_CLIENT_ID",
            "NEO_UCC",
            "TOE_KOTAK_CLIENT_ID",
            "TOE_KOTAK_UCC",
        ),
    )
    NEO_MPIN: str = Field(
        default="",
        validation_alias=AliasChoices("NEO_MPIN", "TOE_KOTAK_MPIN"),
    )
    NEO_TOTP: str = Field(
        default="",
        validation_alias=AliasChoices("NEO_TOTP", "TOE_KOTAK_TOTP"),
    )
    NEO_TOTP_SECRET: str = Field(
        default="",
        validation_alias=AliasChoices("NEO_TOTP_SECRET", "TOE_KOTAK_TOTP_SECRET"),
    )

    MAX_PARALLEL_ORDER_WORKERS: int = 4
    FEED_WAIT_SEC: float = 8.0
    KOTAK_MODIFY_ENABLED: bool = False
    KOTAK_MODIFY_TICKS: int = 1

    # Working-order chase: modify the resting DAY order in place.
    # When False, the single FEED_WAIT_SEC wait and optional one-shot modify are used.
    KOTAK_CHASE_ENABLED: bool = True
    KOTAK_CHASE_POLL_SEC: float = 1.0
    KOTAK_CHASE_MODIFY_SEC: float = 3.0
    KOTAK_ENTRY_CHASE_TICKS: int = 1
    KOTAK_ENTRY_MAX_SLIP_PCT: float = 0.10
    KOTAK_ENTRY_CHASE_MAX_SEC: float = 60.0
    KOTAK_EXIT_CHASE_TICKS: int = 1
    KOTAK_EXIT_MAX_MODIFY_ATTEMPTS: int = 20
    KOTAK_LTP_REFRESH_ENABLED: bool = True
    TOE_KOTAK_SKIP_MARGIN_CHECK: bool = False

    TOE_KOTAK_STARTUP_MAX_ATTEMPTS: int = 10
    TOE_KOTAK_STARTUP_RETRY_BASE_SEC: float = 0.5
    TOE_KOTAK_STARTUP_RETRY_MAX_SEC: float = 8.0


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
