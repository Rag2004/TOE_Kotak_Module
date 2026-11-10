"""Place, modify, cancel, and margin calls on an authenticated Neo session."""
from __future__ import annotations

import logging

from app import session as session_mod
from app.session import KotakSession, SessionExpired
from app.symbol_map import neo_segment

logger = logging.getLogger(__name__)

_ORDER_TYPE = {
    "MARKET": "MKT",
    "MKT": "MKT",
    "LIMIT": "L",
    "L": "L",
    "STOP_LOSS": "SL",
    "SL": "SL",
    "STOP_LOSS_MARKET": "SL-M",
    "SL-M": "SL-M",
}

_PRODUCT = {
    "INTRADAY": "MIS",
    "MIS": "MIS",
    "MARGIN": "NRML",
    "NRML": "NRML",
    "CNC": "CNC",
    "MTF": "MTF",
}


def to_neo_order(intent: dict, trading_symbol: str) -> dict:
    """Desk intent -> Neo place_order keyword arguments."""
    side = str(intent.get("side") or "").strip().upper()
    if side in {"BUY", "B"}:
        transaction = "B"
    elif side in {"SELL", "S"}:
        transaction = "S"
    else:
        raise ValueError(f"unsupported side={side}")

    order_type = _ORDER_TYPE.get(str(intent.get("order_type") or "MARKET").strip().upper(), "MKT")
    product = _PRODUCT.get(str(intent.get("product_type") or "INTRADAY").strip().upper(), "MIS")
    validity = str(intent.get("validity") or "DAY").strip().upper()
    if validity not in {"DAY", "IOC"}:
        validity = "DAY"

    price = _price_text(intent.get("limit_price"))
    if order_type == "MKT":
        price = "0"
    elif _to_float(price) <= 0:
        price = _price_text(intent.get("ltp"))

    trigger = _price_text(intent.get("trigger_price"))
    if order_type not in {"SL", "SL-M"}:
        trigger = "0"

    return {
        "exchange_segment": neo_segment(str(intent.get("segment") or "")),
        "product": product,
        "price": price,
        "order_type": order_type,
        "quantity": str(int(_to_float(intent.get("quantity")))),
        "validity": validity,
        "trading_symbol": trading_symbol,
        "transaction_type": transaction,
        "trigger_price": trigger,
        "disclosed_quantity": "0",
        "amo": "NO",
        "tag": str(intent.get("signal_id") or "") or None,
    }


class KotakBroker:
    def __init__(self, kotak_session: KotakSession | None = None) -> None:
        self.session = kotak_session or session_mod.session

    def place_order(self, intent: dict, trading_symbol: str) -> dict:
        payload = to_neo_order(intent, trading_symbol)
        if payload["order_type"] in {"L", "SL"} and _to_float(payload["price"]) <= 0:
            return _reject("", "NO_LIMIT_REFERENCE", "No limit reference for live order")
        client = self.session.ensure()
        raw = _call(client, "place_order", **{k: v for k, v in payload.items() if v is not None})
        return _normalize_place(raw)

    def modify_order(self, order_id: str, *, price: float, quantity: int, order_type: str = "L", validity: str = "DAY") -> dict:
        client = self.session.ensure()
        neo_type = _ORDER_TYPE.get(order_type.upper(), "L")
        raw = _call(
            client,
            "modify_order",
            order_id=str(order_id),
            price=_price_text(price),
            order_type=neo_type,
            quantity=str(int(quantity)),
            validity=validity,
        )
        normalized = _normalize_place(raw)
        normalized["order_id"] = normalized.get("order_id") or str(order_id)
        return normalized

    def cancel_order(self, order_id: str) -> dict:
        client = self.session.ensure()
        raw = _call(client, "cancel_order", order_id=str(order_id))
        return _normalize_place(raw)

    def order_report(self, order_id: str) -> dict | None:
        client = self.session.ensure()
        raw = _call(client, "order_report")
        rows = raw if isinstance(raw, list) else (raw or {}).get("data") if isinstance(raw, dict) else None
        if not isinstance(rows, list):
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("nOrdNo") or row.get("order_id") or "") == str(order_id):
                return row
        return None

    def check_margin(self, intent: dict, trading_symbol: str, instrument_token: str = "") -> dict:
        from app.config import settings

        if settings.TOE_KOTAK_SKIP_MARGIN_CHECK:
            return {"sufficient": True, "required": 0.0, "available": 0.0, "error": "skipped_pre_margin"}
        client = self.session.ensure()
        payload = to_neo_order(intent, trading_symbol)
        if not hasattr(client, "margin_required"):
            return {"sufficient": True, "required": 0.0, "available": 0.0, "error": "no_margin_api"}
        token = str(instrument_token or "").strip()
        if not token:
            logger.warning("margin_required skipped: missing instrument_token symbol=%s", trading_symbol)
            return {"sufficient": True, "required": 0.0, "available": 0.0, "error": "no_instrument_token"}
        price = payload["price"]
        if _to_float(price) <= 0:
            price = _price_text(intent.get("ltp") or intent.get("fallback_fill_price"))
        try:
            raw = _call(
                client,
                "margin_required",
                exchange_segment=payload["exchange_segment"],
                price=price,
                order_type=payload["order_type"],
                product=payload["product"],
                quantity=payload["quantity"],
                instrument_token=token,
                transaction_type=payload["transaction_type"],
            )
        except (TypeError, AttributeError) as exc:
            logger.warning("margin_required failed open: %s", exc)
            return {"sufficient": True, "required": 0.0, "available": 0.0, "error": str(exc)}
        if isinstance(raw, dict) and _is_forbidden(raw):
            raise SessionExpired("margin_required unauthorized")
        required, available = _margin_amounts(raw)
        if required <= 0 and available <= 0:
            logger.warning("margin_required unparsed; continuing symbol=%s", trading_symbol)
            return {"sufficient": True, "required": 0.0, "available": 0.0, "error": "unparsed"}
        return {
            "sufficient": available + 1e-6 >= required,
            "required": required,
            "available": available,
            "error": "",
        }


def _call(client, method: str, **kwargs):
    fn = getattr(client, method)
    try:
        raw = fn(**kwargs)
    except SessionExpired:
        raise
    except Exception as exc:
        if _is_forbidden(exc):
            raise SessionExpired(str(exc)) from exc
        raise
    if _is_forbidden(raw):
        raise SessionExpired(f"{method} unauthorized")
    return raw


def _normalize_place(raw) -> dict:
    if not isinstance(raw, dict):
        return _reject("", "BROKER_ERROR", "empty broker response")
    order_id = str(raw.get("nOrdNo") or raw.get("order_id") or "")
    stat = str(raw.get("stat") or "").strip().lower()
    status_code = raw.get("status_code") or raw.get("stCode")
    ok = stat == "ok" or status_code in (200, "200")
    if ok and order_id:
        return {"ok": True, "order_id": order_id, "raw": raw}
    reason = str(raw.get("errMsg") or raw.get("message") or raw.get("stat") or "broker rejected")
    broker_status = "BROKER_REJECTED"
    lowered = reason.lower()
    if "margin" in lowered or "fund" in lowered:
        broker_status = "INSUFFICIENT_MARGIN"
    return _reject(order_id, broker_status, reason, raw=raw)


def _reject(order_id: str, broker_status: str, reason: str, raw=None) -> dict:
    return {
        "ok": False,
        "order_id": order_id,
        "broker_status": broker_status,
        "rejection_reason": reason,
        "raw": raw or {},
    }


def _is_forbidden(value) -> bool:
    if isinstance(value, dict):
        code = value.get("status_code") or value.get("stCode")
        if code in (403, "403"):
            return True
        text = str(value.get("errMsg") or value.get("message") or "").lower()
        return "invalid session" in text or "re-login" in text
    text = str(value).lower()
    return "403" in text and ("session" in text or "unauthor" in text or "forbidden" in text)


def _price_text(value) -> str:
    number = _to_float(value)
    if number <= 0:
        return "0"
    return f"{number:.2f}"


def _to_float(value) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _margin_amounts(raw) -> tuple[float, float]:
    """Required is reqdMrgn, else ordMrgn. Available is avlMrgn, else avlCash."""
    body = raw.get("data") if isinstance(raw, dict) and isinstance(raw.get("data"), dict) else raw
    if not isinstance(body, dict):
        return 0.0, 0.0
    required = _to_float(body.get("reqdMrgn"))
    if required <= 0:
        required = _to_float(body.get("ordMrgn"))
    if required <= 0:
        required = _first_float(body, ("reqMargin", "margin", "requiredMargin", "marginUsed"))
    available = _to_float(body.get("avlMrgn"))
    if available <= 0:
        available = _to_float(body.get("avlCash"))
    if available <= 0:
        available = _first_float(body, ("avlMargin", "availableMargin", "available"))
    return required, available


def _first_float(raw, keys: tuple[str, ...]) -> float:
    if not isinstance(raw, dict):
        return 0.0
    for key in keys:
        if key in raw:
            return _to_float(raw.get(key))
    data = raw.get("data")
    if isinstance(data, dict):
        return _first_float(data, keys)
    return 0.0


broker = KotakBroker()
