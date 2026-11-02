"""Redis record of a working Kotak order so a redelivery resumes the chase."""
from __future__ import annotations

import json
import logging

from app.clients import get_redis

logger = logging.getLogger(__name__)

_TTL_SEC = 86400


def inflight_key(signal_id: str) -> str:
    return f"toe_kotak:inflight:{str(signal_id or '').strip()}"


def save_inflight(signal_id: str, order_id: str, quantity: int, anchor: float) -> None:
    signal_id = str(signal_id or "").strip()
    order_id = str(order_id or "").strip()
    if not signal_id or not order_id:
        return
    payload = json.dumps(
        {
            "order_id": order_id,
            "quantity": int(quantity),
            "anchor": float(anchor),
        },
        separators=(",", ":"),
    )
    try:
        get_redis().set(inflight_key(signal_id), payload, ex=_TTL_SEC)
    except Exception as e:
        logger.warning("inflight save failed signal=%s order=%s: %s", signal_id, order_id, e)


def load_inflight(signal_id: str) -> dict | None:
    signal_id = str(signal_id or "").strip()
    if not signal_id:
        return None
    try:
        raw = get_redis().get(inflight_key(signal_id))
    except Exception as e:
        logger.warning("inflight load failed signal=%s: %s", signal_id, e)
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict) or not str(data.get("order_id") or "").strip():
        return None
    return data


def clear_inflight(signal_id: str) -> None:
    signal_id = str(signal_id or "").strip()
    if not signal_id:
        return
    try:
        get_redis().delete(inflight_key(signal_id))
    except Exception as e:
        logger.warning("inflight clear failed signal=%s: %s", signal_id, e)
