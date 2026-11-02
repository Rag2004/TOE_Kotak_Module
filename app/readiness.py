"""Process liveness and readiness."""
from __future__ import annotations

from typing import Any

from app.clients import get_mongo_client, get_redis
from app.config import settings

_consumers_started = False
_startup_error: str | None = None


def mark_consumers_started() -> None:
    global _consumers_started
    _consumers_started = True


def set_startup_error(msg: str | None) -> None:
    global _startup_error
    _startup_error = msg


def liveness() -> dict[str, Any]:
    return {"status": "alive", "service": "TOE_KOTAK", "broker": "kotak_sandbox"}


def readiness() -> dict[str, Any]:
    redis_ok, redis_err = _ping_redis()
    mongo_ok, mongo_err = _ping_mongo()
    ready = bool(redis_ok and mongo_ok and _consumers_started and _startup_error is None)
    return {
        "status": "ready" if ready else "not_ready",
        "service": "TOE_KOTAK",
        "broker": "kotak_sandbox",
        "ready": ready,
        "redis": {"ok": redis_ok, "url": settings.REDIS_URL, "error": redis_err},
        "mongo": {"ok": mongo_ok, "uri": settings.MONGO_URI, "error": mongo_err},
        "consumers_started": _consumers_started,
        "startup_error": _startup_error,
    }


def _ping_redis() -> tuple[bool, str]:
    try:
        get_redis().ping()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _ping_mongo() -> tuple[bool, str]:
    try:
        get_mongo_client().admin.command("ping")
        return True, ""
    except Exception as exc:
        return False, str(exc)
