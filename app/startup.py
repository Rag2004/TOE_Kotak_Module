"""Bounded Redis and Mongo connection retries."""
from __future__ import annotations

import logging
import time

import redis as redis_lib

from app.clients import get_mongo_client, get_redis, reset_clients_for_tests
from app.config import settings

logger = logging.getLogger(__name__)


def _backoff(attempt: int) -> float:
    base = max(0.05, float(settings.TOE_KOTAK_STARTUP_RETRY_BASE_SEC))
    cap = max(base, float(settings.TOE_KOTAK_STARTUP_RETRY_MAX_SEC))
    return min(cap, base * (2 ** max(0, attempt - 1)))


def wait_for_redis() -> redis_lib.Redis:
    attempts = max(1, int(settings.TOE_KOTAK_STARTUP_MAX_ATTEMPTS))
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            client = get_redis()
            client.ping()
            logger.info("Redis connected: %s (attempt %d/%d)", settings.REDIS_URL, attempt, attempts)
            return client
        except Exception as exc:
            last_err = exc
            reset_clients_for_tests()
            delay = _backoff(attempt)
            logger.warning("Redis connect failed attempt %d/%d: %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(delay)
    raise RuntimeError(f"Redis unavailable after {attempts} attempts: {last_err}")


def wait_for_mongo() -> None:
    attempts = max(1, int(settings.TOE_KOTAK_STARTUP_MAX_ATTEMPTS))
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            get_mongo_client().admin.command("ping")
            logger.info("Mongo connected: %s (attempt %d/%d)", settings.MONGO_URI, attempt, attempts)
            return
        except Exception as exc:
            last_err = exc
            reset_clients_for_tests()
            delay = _backoff(attempt)
            logger.warning("Mongo connect failed attempt %d/%d: %s", attempt, attempts, exc)
            if attempt < attempts:
                time.sleep(delay)
    raise RuntimeError(f"Mongo unavailable after {attempts} attempts: {last_err}")
