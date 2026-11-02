"""One place per intent, then chase the working order on the Neo feed."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app import broker as broker_mod
from app import inflight
from app import order_feed as feed_mod
from app.chase import chase_working_order, prepare_chase_intent
from app.config import settings
from app.price_source import (
    PRICE_SOURCE_AVERAGE_TRADED,
    PRICE_SOURCE_MISSING,
    PRICE_SOURCE_REFERENCE_LTP_FALLBACK,
)
from app.session import SessionExpired
from app.state_machine import OrderStateMachine, feed_status_to_state
from app.symbol_map import SymbolNotFound, symbol_map

logger = logging.getLogger(__name__)

_NON_RETRYABLE = {"INSUFFICIENT_MARGIN", "AUTH_FAILED", "SYMBOL_NOT_FOUND", "NO_LIMIT_REFERENCE"}


def dispatch_order(intent: dict) -> dict:
    signal_id = str(intent.get("signal_id") or "")
    machine = OrderStateMachine(signal_id)
    try:
        trading_symbol = symbol_map.resolve(intent)
    except SymbolNotFound as exc:
        machine.transition("REJECTED")
        return _result(intent, "", 0.0, "REJECTED", "SYMBOL_NOT_FOUND", str(exc))

    margin = broker_mod.broker.check_margin(intent, trading_symbol)
    if not margin.get("sufficient", True):
        machine.transition("REJECTED")
        return _result(
            intent,
            "",
            0.0,
            "REJECTED",
            "INSUFFICIENT_MARGIN",
            f"Margin required={margin.get('required', 0):.2f} available={margin.get('available', 0):.2f}",
        )

    if _chase_enabled():
        return _dispatch_chase(intent, machine, trading_symbol)

    return _dispatch_feed_wait(intent, machine, trading_symbol)


def _chase_enabled() -> bool:
    return bool(getattr(settings, "KOTAK_CHASE_ENABLED", False))


def _dispatch_chase(intent: dict, machine: OrderStateMachine, trading_symbol: str) -> dict:
    """Place at most one order and chase it. A redelivery resumes the inflight order id."""
    signal_id = str(intent.get("signal_id") or "")
    existing = inflight.load_inflight(signal_id)
    if existing:
        order_id = str(existing.get("order_id") or "")
        feed_mod.order_feed.register(signal_id, order_id)
        machine.transition("TRANSIT")
        try:
            outcome = chase_working_order(
                intent,
                order_id,
                _qty(existing.get("quantity")),
                _float(existing.get("anchor")),
            )
        finally:
            inflight.clear_inflight(signal_id)
        return _note_chase_result(machine, intent, outcome)

    prep_err = prepare_chase_intent(intent)
    if prep_err:
        machine.transition("REJECTED")
        return _result(
            intent,
            "",
            0.0,
            "REJECTED",
            prep_err,
            "No limit reference (ltp/limit_price/fallback_fill_price) for live order",
        )

    machine.transition("TRANSIT")
    placed = _place_with_session_retry(intent, trading_symbol)
    if not placed.get("ok"):
        machine.transition("REJECTED")
        return _result(
            intent,
            str(placed.get("order_id") or ""),
            0.0,
            "REJECTED",
            str(placed.get("broker_status") or "BROKER_REJECTED"),
            str(placed.get("rejection_reason") or ""),
        )

    order_id = str(placed["order_id"])
    placed_qty = _qty(intent.get("quantity"))
    anchor = _float(intent.get("_chase_anchor"))
    feed_mod.order_feed.register(signal_id, order_id)
    inflight.save_inflight(signal_id, order_id, placed_qty, anchor)
    try:
        outcome = chase_working_order(intent, order_id, placed_qty, anchor)
    finally:
        inflight.clear_inflight(signal_id)
    return _note_chase_result(machine, intent, outcome)


def _note_chase_result(machine: OrderStateMachine, intent: dict, outcome: dict) -> dict:
    status = str(outcome.get("status") or "").upper()
    broker_status = str(outcome.get("broker_status") or "")
    if status == "FILLED":
        if outcome.get("fill_type") == "PARTIAL":
            if machine.current_state == "TRANSIT":
                machine.transition("PENDING")
            machine.transition("PART_TRADED")
        else:
            machine.transition("TRADED")
    elif status == "PENDING":
        machine.transition("PENDING")
    elif broker_status == "CANCELLED":
        machine.transition("CANCELLED")
    else:
        machine.transition("REJECTED")
    return _result(
        intent,
        str(outcome.get("order_id") or ""),
        _float(outcome.get("fill_price")),
        status or "REJECTED",
        broker_status,
        str(outcome.get("rejection_reason") or ""),
        quantity=_qty(outcome.get("quantity")) if outcome.get("quantity") not in (None, "") else None,
        fill_type=outcome.get("fill_type"),
        price_source=outcome.get("price_source"),
    )


def _dispatch_feed_wait(intent: dict, machine: OrderStateMachine, trading_symbol: str) -> dict:
    """Place once and block on the order feed for FEED_WAIT_SEC. Used when chase is off."""
    signal_id = str(intent.get("signal_id") or "")
    machine.transition("TRANSIT")
    placed = _place_with_session_retry(intent, trading_symbol)
    if not placed.get("ok"):
        machine.transition("REJECTED")
        return _result(
            intent,
            str(placed.get("order_id") or ""),
            0.0,
            "REJECTED",
            str(placed.get("broker_status") or "BROKER_REJECTED"),
            str(placed.get("rejection_reason") or ""),
        )

    order_id = str(placed["order_id"])
    feed_mod.order_feed.register(signal_id, order_id)
    update = feed_mod.order_feed.wait(order_id, settings.FEED_WAIT_SEC)
    if _should_modify(update):
        _modify_toward_market(intent, order_id)
        update = feed_mod.order_feed.wait(order_id, settings.FEED_WAIT_SEC)
    if update is None and not feed_mod.order_feed.connected:
        update = _report_as_update(order_id)
    if update is None:
        machine.transition("PENDING")
        return _result(intent, order_id, 0.0, "PENDING", "FEED_TIMEOUT", "")

    state = feed_status_to_state(str(update.get("ordSt") or ""), int(update.get("fldQty") or 0), int(update.get("qty") or 0))
    if state == "PENDING":
        machine.transition("PENDING")
        return _result(intent, order_id, 0.0, "PENDING", str(update.get("ordSt") or "open"), "")
    if state == "REJECTED":
        machine.transition("REJECTED")
        return _result(intent, order_id, 0.0, "REJECTED", "REJECTED", str(update.get("ordSt") or "rejected"))
    if state == "CANCELLED":
        machine.transition("CANCELLED")
        return _result(intent, order_id, 0.0, "REJECTED", "CANCELLED", "cancelled")

    filled_qty = int(update.get("fldQty") or intent.get("quantity") or 0)
    avg = float(update.get("avgPrc") or 0.0)
    price_source = PRICE_SOURCE_AVERAGE_TRADED if avg > 0 else PRICE_SOURCE_MISSING
    if avg <= 0:
        ltp = _float(intent.get("ltp"))
        if ltp > 0:
            avg = ltp
            price_source = PRICE_SOURCE_REFERENCE_LTP_FALLBACK
    fill_type = "PARTIAL" if state == "PART_TRADED" else "FULL"
    if machine.current_state == "TRANSIT" and state == "PART_TRADED":
        machine.transition("PART_TRADED")
    elif state == "TRADED":
        machine.transition("TRADED")
    elif state == "PART_TRADED":
        machine.transition("PART_TRADED")
    return _result(
        intent,
        order_id,
        avg,
        "FILLED",
        "NEO_COMPLETE",
        "",
        quantity=filled_qty,
        fill_type=fill_type,
        price_source=price_source,
    )


def _place_with_session_retry(intent: dict, trading_symbol: str) -> dict:
    try:
        return broker_mod.broker.place_order(intent, trading_symbol)
    except SessionExpired:
        logger.warning("Neo session expired on place; refreshing")
        broker_mod.broker.session.refresh()
        feed_mod.order_feed.restart()
        return broker_mod.broker.place_order(intent, trading_symbol)


def _should_modify(update: dict | None) -> bool:
    if not settings.KOTAK_MODIFY_ENABLED or not update:
        return False
    return str(update.get("ordSt") or "").strip().lower() == "open"


def _modify_toward_market(intent: dict, order_id: str) -> None:
    tick = 0.05 * max(1, int(settings.KOTAK_MODIFY_TICKS))
    live = _float(intent.get("ltp")) or _float(intent.get("limit_price"))
    if live <= 0:
        return
    side = str(intent.get("side") or "BUY").upper()
    price = live + tick if side == "BUY" else max(tick, live - tick)
    quantity = int(_float(intent.get("quantity")))
    logger.info("NEO_MODIFY order=%s price=%.2f", order_id, price)
    broker_mod.broker.modify_order(order_id, price=price, quantity=quantity, order_type="L", validity="DAY")


def _report_as_update(order_id: str) -> dict | None:
    row = broker_mod.broker.order_report(order_id)
    if not row:
        return None
    return {
        "nOrdNo": str(row.get("nOrdNo") or order_id),
        "ordSt": str(row.get("ordSt") or row.get("orderStatus") or ""),
        "fldQty": int(_float(row.get("fldQty") or row.get("filledQty") or 0)),
        "qty": int(_float(row.get("qty") or 0)),
        "avgPrc": _float(row.get("avgPrc") or row.get("avgPrc") or 0),
        "GuiOrdId": str(row.get("GuiOrdId") or ""),
    }


def _result(
    intent: dict,
    order_id: str,
    fill_price: float,
    status: str,
    broker_status: str,
    rejection_reason: str,
    *,
    quantity: int | None = None,
    fill_type: str | None = None,
    price_source: str | None = None,
) -> dict:
    if broker_status in _NON_RETRYABLE:
        logger.warning("Order rejected (no retry): %s", broker_status)
    req_qty = int(_float(intent.get("quantity")))
    qty = int(quantity) if quantity is not None else req_qty
    if status == "FILLED":
        ft = str(fill_type or "FULL").upper()
        if ft not in {"FULL", "PARTIAL"}:
            ft = "FULL"
        filled_at = datetime.now(timezone.utc).isoformat()
    else:
        ft = "NONE"
        filled_at = ""
    return {
        "order_id": order_id,
        "signal_id": intent.get("signal_id", ""),
        "strategy_id": intent.get("strategy_id", ""),
        "security_id": intent.get("security_id", "0"),
        "side": intent.get("side", ""),
        "quantity": qty,
        "fill_price": fill_price,
        "status": status,
        "fill_type": ft,
        "broker_status": broker_status,
        "rejection_reason": rejection_reason if status != "FILLED" else "",
        "filled_at": filled_at,
        "underlying_scrip": intent.get("underlying_scrip", "0"),
        "segment": intent.get("segment", ""),
        "strike": intent.get("strike", "0"),
        "option_type": intent.get("option_type", "CE"),
        "expiry": intent.get("expiry", ""),
        "order_class": intent.get("order_class", "ENTRY"),
        "order_tag": intent.get("order_tag", ""),
        "price_source": price_source or PRICE_SOURCE_MISSING,
        "raw_traded_price": fill_price if price_source == PRICE_SOURCE_AVERAGE_TRADED else 0.0,
        "raw_average_traded_price": fill_price if price_source == PRICE_SOURCE_AVERAGE_TRADED else 0.0,
    }


def _float(value) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _qty(value) -> int:
    return int(_float(value))
