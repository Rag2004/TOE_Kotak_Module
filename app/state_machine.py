"""Order lifecycle aligned with the states the desk and the Neo feed share."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

TERMINAL_STATES = {"TRADED", "REJECTED", "CANCELLED", "EXPIRED"}

VALID_TRANSITIONS = {
    None: ["TRANSIT", "REJECTED"],
    "TRANSIT": ["PENDING", "TRADED", "REJECTED", "CANCELLED", "EXPIRED", "PART_TRADED"],
    "PENDING": ["TRADED", "REJECTED", "CANCELLED", "EXPIRED", "PART_TRADED"],
    "PART_TRADED": ["TRADED", "CANCELLED", "PART_TRADED"],
    "TRADED": [],
    "REJECTED": ["TRANSIT"],
    "CANCELLED": [],
    "EXPIRED": [],
}

_order_states: dict[str, list[dict]] = {}


class OrderStateMachine:
    def __init__(self, signal_id: str):
        self.signal_id = signal_id
        self.current_state: str | None = None
        if signal_id not in _order_states:
            _order_states[signal_id] = []

    def transition(self, new_state: str) -> None:
        valid = VALID_TRANSITIONS.get(self.current_state, [])
        if new_state not in valid:
            logger.warning(
                "Invalid state transition: %s -> %s (signal=%s)",
                self.current_state,
                new_state,
                self.signal_id,
            )
        _order_states[self.signal_id].append(
            {
                "from": self.current_state,
                "to": new_state,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
        self.current_state = new_state

    def is_terminal(self) -> bool:
        return self.current_state in TERMINAL_STATES


def feed_status_to_state(order_status: str, filled_qty: int, order_qty: int) -> str:
    """Map a Neo ordSt string onto the desk state machine."""
    status = str(order_status or "").strip().lower()
    if status in {"rejected"}:
        return "REJECTED"
    if status in {"cancelled", "canceled"}:
        return "CANCELLED"
    if status in {"complete", "traded"}:
        if order_qty > 0 and 0 < filled_qty < order_qty:
            return "PART_TRADED"
        return "TRADED"
    if filled_qty > 0 and (order_qty <= 0 or filled_qty < order_qty):
        return "PART_TRADED"
    if status in {"open", "open pending", "put order req received", "validation pending", "modified"}:
        return "PENDING"
    return "PENDING"


def get_all_order_states() -> dict[str, list[dict]]:
    return _order_states.copy()


def reset_order_states_for_tests() -> None:
    _order_states.clear()
