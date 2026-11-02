"""In-place Kotak working-order chase.

Wait on the Neo order feed about once a second. Every few seconds, modify
price on the same order id. Entry limits stay inside a band around the signal
price. Exit limits follow the market. One execution is returned when the chase ends.
"""
from __future__ import annotations

import logging
import time

from app import broker as broker_mod
from app import order_feed as feed_mod
from app.config import settings
from app.market_consumer import read_live_ltp
from app.order_feed import is_terminal_update
from app.price_source import (
    PRICE_SOURCE_AVERAGE_TRADED,
    PRICE_SOURCE_MISSING,
    PRICE_SOURCE_REFERENCE_LTP_FALLBACK,
)
from app.session import SessionExpired
from app.state_machine import feed_status_to_state

logger = logging.getLogger(__name__)

_TICK = 0.05
_ENTRY_SLIP_CAP = 0.10


def chase_anchor(intent: dict) -> float:
    """Signal price locked at the start of the chase: limit, then ltp, then fallback."""
    for key in ("limit_price", "ltp", "fallback_fill_price"):
        value = _float(intent.get(key))
        if value > 0:
            return value
    return 0.0


def entry_slip(intent: dict) -> float:
    """Per-order entry band, never above 10%."""
    default = float(getattr(settings, "KOTAK_ENTRY_MAX_SLIP_PCT", _ENTRY_SLIP_CAP))
    raw = intent.get("entry_max_slip_pct")
    if raw not in (None, ""):
        try:
            default = float(raw)
        except (TypeError, ValueError):
            pass
    if default < 0:
        default = 0.0
    return min(default, _ENTRY_SLIP_CAP)


def live_reference(intent: dict, anchor: float) -> float:
    """Live tick key, then the intent's own ltp. The working limit is not a quote."""
    if getattr(settings, "KOTAK_LTP_REFRESH_ENABLED", True):
        try:
            live = read_live_ltp(intent)
        except Exception as e:
            logger.debug("chase live tick read failed: %s", e)
            live = None
        if live is not None and float(live) > 0:
            return float(live)
    ltp = _float(intent.get("ltp"))
    if ltp > 0:
        return ltp
    fallback = _float(intent.get("fallback_fill_price"))
    if fallback > 0:
        return fallback
    return float(anchor or 0.0)


def entry_limit_price(intent: dict, anchor: float, live: float) -> float:
    """Step toward the market by entry ticks, then clamp to the signal band."""
    tick = _TICK
    steps = max(0, int(getattr(settings, "KOTAK_ENTRY_CHASE_TICKS", 1)))
    slip = entry_slip(intent)
    side = str(intent.get("side", "BUY")).upper()
    if side == "SELL":
        raw = live - steps * tick
        floor = anchor * (1.0 - slip)
        if floor > 0:
            raw = max(raw, floor)
    else:
        raw = live + steps * tick
        ceiling = anchor * (1.0 + slip)
        if ceiling > 0:
            raw = min(raw, ceiling)
    if raw <= 0:
        raw = anchor if anchor > 0 else tick
    return _round_opt_tick(raw, tick)


def exit_limit_price(intent: dict, live: float) -> float:
    """Follow the market by exit ticks. No percentage cap."""
    tick = _TICK
    steps = max(0, int(getattr(settings, "KOTAK_EXIT_CHASE_TICKS", 1)))
    side = str(intent.get("side", "BUY")).upper()
    if side == "SELL":
        raw = live - steps * tick
    else:
        raw = live + steps * tick
    if raw <= 0:
        raw = live if live > 0 else tick
    return _round_opt_tick(raw, tick)


def prepare_chase_intent(intent: dict) -> str | None:
    """Set a DAY limit on the intent. Returns a broker_status error, or None."""
    anchor = chase_anchor(intent)
    if anchor <= 0:
        return "NO_LIMIT_REFERENCE"
    intent["order_type"] = "LIMIT"
    intent["validity"] = "DAY"
    if _is_exit(intent):
        intent["limit_price"] = exit_limit_price(intent, live_reference(intent, anchor))
    else:
        intent["limit_price"] = _round_opt_tick(anchor, _TICK)
    intent["_chase_anchor"] = float(anchor)
    return None


def chase_working_order(intent: dict, order_id: str, placed_qty: int, anchor: float) -> dict:
    """Wait on the feed and modify until the order ends or the entry/exit budget is spent."""
    order_id = str(order_id or "").strip()
    placed_qty = int(placed_qty)
    anchor = float(anchor or 0.0)
    is_exit = _is_exit(intent)
    poll = float(getattr(settings, "KOTAK_CHASE_POLL_SEC", 1.0) or 0.0)
    if poll <= 0:
        poll = 0.05
    modify_every = float(getattr(settings, "KOTAK_CHASE_MODIFY_SEC", 3.0) or 0.0)
    if modify_every < 0:
        modify_every = 0.0
    entry_max = float(getattr(settings, "KOTAK_ENTRY_CHASE_MAX_SEC", 60.0) or 0.0)
    exit_max = max(1, int(getattr(settings, "KOTAK_EXIT_MAX_MODIFY_ATTEMPTS", 20) or 1))

    started = time.monotonic()
    next_modify = started + modify_every
    modify_attempts = 0
    resting = _float(intent.get("limit_price"))

    while True:
        update = _await_status(order_id, poll)
        done = _result_from_update(intent, order_id, update, placed_qty)
        if done is not None:
            return done

        now = time.monotonic()
        if not is_exit and entry_max > 0 and (now - started) >= entry_max:
            return _give_up(intent, order_id, placed_qty, "KOTAK_ENTRY_CHASE_EXHAUSTED")
        if is_exit and modify_attempts >= exit_max:
            return _give_up(intent, order_id, placed_qty, "KOTAK_EXIT_CHASE_EXHAUSTED")

        snap_px = _resting_price(update)
        if snap_px > 0:
            resting = snap_px

        if now >= next_modify:
            next_modify = now + modify_every
            if is_exit:
                modify_attempts += 1
            live = live_reference(intent, anchor)
            if is_exit:
                new_px = exit_limit_price(intent, live)
            else:
                new_px = entry_limit_price(intent, anchor, live)
            if _same_price(new_px, resting):
                logger.info(
                    "CHASE skip unchanged price order=%s class=%s px=%.4f attempts=%d",
                    order_id,
                    "EXIT" if is_exit else "ENTRY",
                    new_px,
                    modify_attempts,
                )
            else:
                modified = _broker(lambda: broker_mod.broker.modify_order(
                    order_id,
                    price=new_px,
                    quantity=placed_qty,
                    order_type="L",
                    validity="DAY",
                ))
                if not modified.get("ok"):
                    confirmed = _confirm_status(order_id)
                    finished = _result_from_update(intent, order_id, confirmed, placed_qty)
                    if finished is not None:
                        return finished
                    return _give_up(intent, order_id, placed_qty, "KOTAK_MODIFY_REJECTED")
                resting = new_px
                intent["limit_price"] = new_px
                logger.info(
                    "CHASE modify order=%s class=%s px=%.4f qty=%d attempts=%d",
                    order_id,
                    "EXIT" if is_exit else "ENTRY",
                    new_px,
                    placed_qty,
                    modify_attempts,
                )


def _await_status(order_id: str, poll: float) -> dict | None:
    """Next feed update, or an order report when the socket is down and the feed is empty."""
    update = feed_mod.order_feed.wait(order_id, poll)
    if update is None and not feed_mod.order_feed.connected:
        return _report_as_update(order_id)
    return update


def _confirm_status(order_id: str) -> dict | None:
    """Prefer a terminal feed frame, then the order book, so a cancel still reports fills."""
    latest = feed_mod.order_feed.latest(order_id)
    if latest and is_terminal_update(latest):
        return latest
    reported = _report_as_update(order_id)
    if reported:
        return reported
    return latest


def _give_up(intent: dict, order_id: str, placed_qty: int, reason: str) -> dict:
    """Cancel a still-working order and publish whatever actually traded."""
    logger.info("CHASE give-up order=%s reason=%s", order_id, reason)
    _broker(lambda: broker_mod.broker.cancel_order(order_id))
    update = _confirm_status(order_id)
    done = _result_from_update(intent, order_id, update, placed_qty)
    if done is not None and done.get("status") == "FILLED":
        return done
    return _outcome(
        order_id,
        "REJECTED",
        reason,
        f"Chase stopped ({reason}) for order {order_id}",
        placed_qty,
        0.0,
        "NONE",
        PRICE_SOURCE_MISSING,
    )


def _broker(action):
    """Refresh the Neo session once when a chase call loses its login."""
    try:
        return action()
    except SessionExpired:
        logger.warning("Neo session expired during chase; refreshing")
        broker_mod.broker.session.refresh()
        feed_mod.order_feed.restart()
        return action()


def _report_as_update(order_id: str) -> dict | None:
    row = _broker(lambda: broker_mod.broker.order_report(order_id))
    if not row:
        return None
    return {
        "nOrdNo": str(row.get("nOrdNo") or order_id),
        "ordSt": str(row.get("ordSt") or row.get("orderStatus") or ""),
        "fldQty": _qty(row.get("fldQty") if row.get("fldQty") not in (None, "") else row.get("filledQty")),
        "qty": _qty(row.get("qty")),
        "avgPrc": _float(row.get("avgPrc")),
        "prc": _resting_price(row),
        "GuiOrdId": str(row.get("GuiOrdId") or ""),
    }


def _result_from_update(intent: dict, order_id: str, update: dict | None, placed_qty: int) -> dict | None:
    """A terminal Neo status becomes the single execution. Open statuses do not."""
    if not update or not is_terminal_update(update):
        return None
    filled = _qty(update.get("fldQty"))
    order_qty = _qty(update.get("qty"))
    state = feed_status_to_state(str(update.get("ordSt") or ""), filled, order_qty)
    if filled > 0 or state in {"TRADED", "PART_TRADED"}:
        qty = filled if filled > 0 else placed_qty
        if qty <= 0:
            qty = placed_qty
        fill_type = "PARTIAL" if state == "PART_TRADED" or (placed_qty > 0 and qty < placed_qty) else "FULL"
        price, source = _fill_price(intent, update)
        return _outcome(order_id, "FILLED", "NEO_COMPLETE", "", qty, price, fill_type, source)
    if state == "CANCELLED":
        return _outcome(order_id, "REJECTED", "CANCELLED", "cancelled", placed_qty, 0.0, "NONE", PRICE_SOURCE_MISSING)
    return _outcome(
        order_id,
        "REJECTED",
        "REJECTED",
        str(update.get("ordSt") or "rejected"),
        placed_qty,
        0.0,
        "NONE",
        PRICE_SOURCE_MISSING,
    )


def _fill_price(intent: dict, update: dict) -> tuple[float, str]:
    avg = _float(update.get("avgPrc"))
    if avg > 0:
        return avg, PRICE_SOURCE_AVERAGE_TRADED
    ltp = _float(intent.get("ltp"))
    if ltp > 0:
        return ltp, PRICE_SOURCE_REFERENCE_LTP_FALLBACK
    return 0.0, PRICE_SOURCE_MISSING


def _outcome(
    order_id: str,
    status: str,
    broker_status: str,
    rejection_reason: str,
    quantity: int,
    fill_price: float,
    fill_type: str,
    price_source: str,
) -> dict:
    return {
        "order_id": order_id,
        "status": status,
        "broker_status": broker_status,
        "rejection_reason": rejection_reason,
        "quantity": int(quantity),
        "fill_price": float(fill_price),
        "fill_type": fill_type,
        "price_source": price_source,
    }


def _is_exit(intent: dict) -> bool:
    return str(intent.get("order_class", "ENTRY")).upper() == "EXIT"


def _same_price(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) < 0.001


def _resting_price(update: dict | None) -> float:
    if not isinstance(update, dict):
        return 0.0
    for key in ("prc", "price", "orderPrice"):
        value = _float(update.get(key))
        if value > 0:
            return value
    return 0.0


def _round_opt_tick(price: float, tick: float = 0.05) -> float:
    if price <= 0:
        return tick
    stepped = max(tick, round(price / tick) * tick)
    return round(stepped, 2)


def _float(value) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _qty(value) -> int:
    return int(_float(value))
