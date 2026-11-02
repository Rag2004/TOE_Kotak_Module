"""Read live option ticks written by the Live Data websocket.

The feed stores each contract as a Redis string, for example
``NIFTY-14Oct2026-24500-CE``, and lets the key expire. There is no tick
stream to block on, so this reader GETs the contract key when a price is needed.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

from app.clients import get_redis

logger = logging.getLogger(__name__)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_SYMBOL_ALIASES = {
    "NIFTY": "NIFTY",
    "NIFTY 50": "NIFTY",
    "NIFTY50": "NIFTY",
    "SENSEX": "SENSEX",
}
_SCRIP_SYMBOLS = {13: "NIFTY", 15: "SENSEX", 1: "SENSEX"}


def contract_key(intent: dict) -> str | None:
    """Desk contract name, or None when the intent cannot name one."""
    symbol = _symbol(intent)
    expiry = _expiry_token(intent.get("expiry"))
    strike = _strike_token(intent.get("strike"))
    option_type = str(intent.get("option_type") or "").strip().upper()
    if not symbol or not expiry or not strike or option_type not in ("CE", "PE"):
        return None
    return f"{symbol}-{expiry}-{strike}-{option_type}"


def read_live_ltp(intent: dict) -> float | None:
    """Return the feed LTP for this intent, or None when the key is missing or unusable."""
    key = contract_key(intent)
    if not key:
        return None
    try:
        raw = get_redis().get(key)
    except Exception as exc:
        logger.debug("live tick read failed key=%s err=%s", key, exc)
        return None
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    try:
        body = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(body, dict):
        return None
    if not _security_matches(intent, body.get("security_id")):
        return None
    try:
        ltp = float(body.get("ltp") or 0)
    except (TypeError, ValueError):
        return None
    if ltp <= 0:
        return None
    return ltp


def _symbol(intent: dict) -> str | None:
    raw = str(intent.get("underlying") or "").strip().upper()
    if raw:
        alias = _SYMBOL_ALIASES.get(raw)
        if alias:
            return alias
        if " " not in raw:
            return raw
        return None
    try:
        scrip = int(float(intent.get("underlying_scrip")))
    except (TypeError, ValueError):
        return None
    return _SCRIP_SYMBOLS.get(scrip)


def _expiry_token(expiry) -> str | None:
    text = str(expiry or "").strip()
    if len(text) < 10:
        return None
    try:
        day = date.fromisoformat(text[:10])
    except ValueError:
        try:
            day = datetime.strptime(text[:11], "%d%b%Y").date()
        except ValueError:
            return None
    return f"{day.day:02d}{_MONTHS[day.month - 1]}{day.year}"


def _strike_token(strike) -> str | None:
    if strike in (None, ""):
        return None
    try:
        value = float(strike)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value.is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _security_matches(intent: dict, stored) -> bool:
    """A stored security id, when present, must be the intent's contract."""
    if stored in (None, ""):
        return True
    wanted = intent.get("security_id")
    if wanted in (None, ""):
        return True
    left = str(stored).strip()
    right = str(wanted).strip()
    if left == right:
        return True
    try:
        return int(float(left)) == int(float(right))
    except (TypeError, ValueError):
        return False
