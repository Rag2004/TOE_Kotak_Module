"""Redis record of a working Kotak order so a redelivery resumes the chase."""
from __future__ import annotations

import json
import logging

from app.clients import get_redis

logger = logging.getLogger(__name__)

_TTL_SEC = 86400


_INTENT_FIELDS = (
    "signal_id",
    "strategy_id",
    "security_id",
    "side",
    "quantity",
    "order_class",
    "order_tag",
    "order_type",
    "limit_price",
    "ltp",
    "fallback_fill_price",
    "entry_max_slip_pct",
    "underlying",
    "underlying_scrip",
    "segment",
    "strike",
    "option_type",
    "expiry",
    "leg_role",
    "exit_type",
    "hedge_group_id",
    "sequence",
    "wave",
    "leg_group_id",
    "cycle_id",
)


def inflight_key(signal_id: str) -> str:
    return f"toe_kotak:inflight:{str(signal_id or '').strip()}"


def order_inflight_key(order_id: str) -> str:
    return f"toe_kotak:inflight_order:{str(order_id or '').strip()}"


def _intent_snapshot(intent: dict | None) -> dict:
    """Fields chase needs if this process restarts before the order ends."""
    if not isinstance(intent, dict):
        return {}
    out: dict = {}
    for key in _INTENT_FIELDS:
        value = intent.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, (str, int, float, bool)):
            out[key] = value
        else:
            out[key] = str(value)
    return out


def save_inflight(
    signal_id: str,
    order_id: str,
    quantity: int,
    anchor: float,
    security_id: str = "",
    intent: dict | None = None,
) -> None:
    signal_id = str(signal_id or "").strip()
    order_id = str(order_id or "").strip()
    if not signal_id or not order_id:
        return
    payload = json.dumps(
        {
            "signal_id": signal_id,
            "order_id": order_id,
            "quantity": int(quantity),
            "anchor": float(anchor),
            "security_id": str(security_id or ""),
            "intent": _intent_snapshot(intent),
        },
        separators=(",", ":"),
    )
    try:
        client = get_redis()
        client.set(inflight_key(signal_id), payload, ex=_TTL_SEC)
        client.set(order_inflight_key(order_id), payload, ex=_TTL_SEC)
    except Exception as e:
        logger.warning("inflight save failed signal=%s order=%s: %s", signal_id, order_id, e)


def _load_payload(key: str) -> dict | None:
    try:
        raw = get_redis().get(key)
    except Exception as e:
        logger.warning("inflight load failed key=%s: %s", key, e)
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


def load_inflight(signal_id: str) -> dict | None:
    signal_id = str(signal_id or "").strip()
    if not signal_id:
        return None
    return _load_payload(inflight_key(signal_id))


def load_inflight_by_order(order_id: str) -> dict | None:
    order_id = str(order_id or "").strip()
    if not order_id:
        return None
    return _load_payload(order_inflight_key(order_id))


def clear_inflight(signal_id: str) -> None:
    signal_id = str(signal_id or "").strip()
    if not signal_id:
        return
    order_id = ""
    existing = load_inflight(signal_id)
    if existing:
        order_id = str(existing.get("order_id") or "")
    try:
        client = get_redis()
        client.delete(inflight_key(signal_id))
        if order_id:
            client.delete(order_inflight_key(order_id))
    except Exception as e:
        logger.warning("inflight clear failed signal=%s: %s", signal_id, e)
