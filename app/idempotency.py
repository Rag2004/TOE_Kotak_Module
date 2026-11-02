"""Skip a signal_id that this process already published today."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.clients import get_redis

logger = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _get_redis():
    return get_redis()


def _key() -> str:
    today = datetime.now(IST).strftime("%Y%m%d")
    return f"toe_kotak:processed:{today}"


def is_already_processed(signal_id: str) -> bool:
    if not signal_id:
        return False
    return bool(_get_redis().sismember(_key(), signal_id))


def mark_processed(signal_id: str) -> None:
    if not signal_id:
        return
    redis = _get_redis()
    key = _key()
    redis.sadd(key, signal_id)
    redis.expire(key, 86400)
    logger.info("IDEMPOTENCY MARK signal=%s", signal_id)
