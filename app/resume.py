"""Resume chase on an order that is already working.

Re-run must not place a second entry. The desk sends the working order id here.
The inflight payload saved at place time supplies the signal, quantity, and anchor.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

from app import chase
from app import inflight
from app import order_feed as feed_mod

logger = logging.getLogger(__name__)


def resume_working_order(order_id: str) -> dict:
    """Start chase_working_order again for this order id. Do not place."""
    order_id = str(order_id or "").strip()
    row = inflight.load_inflight_by_order(order_id)
    if not row:
        return {
            "ok": False,
            "order_id": order_id,
            "resumed": False,
            "reason": "NO_INFLIGHT",
        }
    if not chase.reserve_chase(order_id):
        return {
            "ok": True,
            "order_id": order_id,
            "resumed": False,
            "reason": "ALREADY_CHASING",
            "signal_id": str(row.get("signal_id") or ""),
        }
    intent = _intent_from_row(row)
    thread = threading.Thread(
        target=_resume_chase,
        args=(
            intent,
            order_id,
            int(_float(row.get("quantity"))),
            _float(row.get("anchor")),
        ),
        daemon=True,
        name=f"kotak-resume-{order_id}",
    )
    thread.start()
    return {
        "ok": True,
        "order_id": order_id,
        "resumed": True,
        "reason": "WORKING",
        "signal_id": str(intent.get("signal_id") or ""),
    }


def _resume_chase(intent: dict, order_id: str, quantity: int, anchor: float) -> None:
    from app.consumer import _execution_publish_merge
    from app.dispatcher import _note_chase_result
    from app.publisher import publish_execution
    from app.state_machine import OrderStateMachine

    signal_id = str(intent.get("signal_id") or "")
    feed_mod.order_feed.register(signal_id, order_id)
    machine = OrderStateMachine(signal_id)
    machine.transition("TRANSIT")
    try:
        outcome = chase.chase_working_order(
            intent,
            order_id,
            quantity,
            anchor,
            reserved=True,
        )
    except Exception:
        chase.release_chase(order_id)
        logger.exception("resume chase failed order=%s", order_id)
        return
    if str(outcome.get("broker_status") or "") == "ALREADY_CHASING":
        return
    try:
        result = _note_chase_result(machine, intent, outcome)
        publish_execution(
            _execution_publish_merge(result, intent),
            datetime.now(timezone.utc).timestamp(),
        )
    except Exception:
        logger.exception("resume execution publish failed order=%s", order_id)
        return
    inflight.clear_inflight(signal_id)


def _intent_from_row(row: dict) -> dict:
    intent = dict(row.get("intent") or {})
    intent["signal_id"] = str(row.get("signal_id") or intent.get("signal_id") or "")
    security_id = str(row.get("security_id") or intent.get("security_id") or "")
    if security_id:
        intent["security_id"] = security_id
    quantity = int(_float(row.get("quantity") or intent.get("quantity")))
    intent["quantity"] = quantity
    anchor = _float(row.get("anchor"))
    if anchor > 0 and _float(intent.get("limit_price")) <= 0:
        intent["limit_price"] = anchor
    if anchor > 0 and _float(intent.get("ltp")) <= 0:
        intent["ltp"] = anchor
    intent.setdefault("side", "BUY")
    intent.setdefault("order_class", "ENTRY")
    return intent


def _float(value) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0
