"""Neo order feed. OrderUpdate frames are the fill and status source."""
from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_TERMINAL_STATUS = {"complete", "traded", "rejected", "cancelled", "canceled"}


def _as_dict(message: Any) -> dict:
    if isinstance(message, dict):
        data = message.get("data") if isinstance(message.get("data"), dict) else message
        return dict(data)
    data = getattr(message, "data", None)
    if data is not None and hasattr(data, "model_dump"):
        dumped = data.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if hasattr(message, "model_dump"):
        dumped = message.model_dump()
        if isinstance(dumped, dict):
            nested = dumped.get("data")
            return dict(nested) if isinstance(nested, dict) else dumped
    return {}


def normalize_update(message: Any) -> dict:
    """Flatten an OrderUpdate model or a raw frame onto Neo wire names."""
    raw = _as_dict(message)
    order_id = str(raw.get("nOrdNo") or raw.get("order_id") or "")
    status = str(raw.get("ordSt") or raw.get("order_status") or "")
    filled = _int(raw.get("fldQty") if raw.get("fldQty") not in (None, "") else raw.get("filled_quantity"))
    quantity = _int(raw.get("qty") if raw.get("qty") not in (None, "") else raw.get("quantity"))
    avg = _float(raw.get("avgPrc") if raw.get("avgPrc") not in (None, "") else raw.get("average_price"))
    tag = str(raw.get("GuiOrdId") or raw.get("gui_ord_id") or raw.get("tag") or "")
    return {
        "nOrdNo": order_id,
        "ordSt": status,
        "fldQty": filled,
        "qty": quantity,
        "avgPrc": avg,
        "GuiOrdId": tag,
    }


def is_terminal_update(update: dict) -> bool:
    return str(update.get("ordSt") or "").strip().lower() in _TERMINAL_STATUS


class OrderFeed:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiters: dict[str, threading.Event] = {}
        self._updates: dict[str, dict] = {}
        self._generation: dict[str, int] = {}
        self._seen: dict[str, int] = {}
        self._signal_to_order: dict[str, str] = {}
        self.connected = False
        self.restart_count = 0
        self._stop = threading.Event()

    def reset_for_tests(self) -> None:
        with self._lock:
            self._waiters.clear()
            self._updates.clear()
            self._generation.clear()
            self._seen.clear()
            self._signal_to_order.clear()
        self.connected = False
        self.restart_count = 0
        self._stop.clear()

    def register(self, signal_id: str, order_id: str) -> None:
        with self._lock:
            self._signal_to_order[str(signal_id)] = str(order_id)
            self._waiters.setdefault(str(order_id), threading.Event())

    def handle(self, message: Any) -> dict | None:
        update = normalize_update(message)
        order_id = update["nOrdNo"]
        if not order_id and update["GuiOrdId"]:
            with self._lock:
                order_id = self._signal_to_order.get(update["GuiOrdId"], "")
            update["nOrdNo"] = order_id
        if not order_id:
            logger.info("ORDER_FEED unmatched status=%s", update.get("ordSt"))
            return None
        with self._lock:
            self._updates[order_id] = update
            self._generation[order_id] = self._generation.get(order_id, 0) + 1
            waiter = self._waiters.setdefault(order_id, threading.Event())
            waiter.set()
        logger.info(
            "ORDER_FEED order=%s status=%s filled=%s avg=%s",
            order_id,
            update.get("ordSt"),
            update.get("fldQty"),
            update.get("avgPrc"),
        )
        return update

    def latest(self, order_id: str) -> dict | None:
        with self._lock:
            found = self._updates.get(str(order_id))
            return dict(found) if found else None

    def wait(self, order_id: str, timeout: float) -> dict | None:
        """Return the next unseen update, or the latest after timeout."""
        order_id = str(order_id)
        with self._lock:
            generation = self._generation.get(order_id, 0)
            seen = self._seen.get(order_id, 0)
            existing = self._updates.get(order_id)
            if existing and generation > seen:
                self._seen[order_id] = generation
                return dict(existing)
            waiter = self._waiters.setdefault(order_id, threading.Event())
            waiter.clear()
        if not waiter.wait(max(0.0, float(timeout))):
            return self.latest(order_id)
        with self._lock:
            self._seen[order_id] = self._generation.get(order_id, 0)
        return self.latest(order_id)

    def restart(self) -> None:
        self.restart_count += 1
        self.connected = False
        self._stop.set()
        logger.info("ORDER_FEED restart count=%d", self.restart_count)

    def run(self, messages) -> None:
        """Consume an iterator of OrderUpdate frames. Used by the socket loop and tests."""
        self._stop.clear()
        self.connected = True
        try:
            for message in messages:
                if self._stop.is_set():
                    break
                self.handle(message)
        finally:
            self.connected = False

    def serve(self, client) -> None:
        """Block on Neo create_order_feed() until restart() or the socket ends."""
        import asyncio

        if not hasattr(client, "create_order_feed"):
            raise RuntimeError("Neo client has no create_order_feed")

        async def _main() -> None:
            async with client.create_order_feed() as feed:
                self._stop.clear()
                self.connected = True
                try:
                    async for message in feed:
                        if self._stop.is_set():
                            break
                        self.handle(message)
                finally:
                    self.connected = False

        asyncio.run(_main())


def _int(value) -> int:
    try:
        if value in (None, ""):
            return 0
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _float(value) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


order_feed = OrderFeed()
