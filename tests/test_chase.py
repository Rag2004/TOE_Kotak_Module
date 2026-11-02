"""Working-order chase: modify in place, entry band, exit past four attempts."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.config import Settings
from app.order_feed import order_feed
from app.session import SessionExpired
from app.state_machine import reset_order_states_for_tests
from app.symbol_map import symbol_map
from tests.test_dispatcher import INTENT
from tests.test_symbol_map import SENSEX_ROW


class _Clock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += float(seconds)


class _Redis:
    def __init__(self):
        self.kv: dict[str, str] = {}

    def set(self, key, value, ex=None):
        self.kv[key] = value
        return True

    def get(self, key):
        return self.kv.get(key)

    def delete(self, key):
        self.kv.pop(key, None)
        return 1


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    symbol_map.load_rows([SENSEX_ROW])
    order_feed.reset_for_tests()
    order_feed.connected = True
    reset_order_states_for_tests()
    monkeypatch.setattr("app.chase.broker_mod.broker.order_report", lambda order_id: None)
    yield
    order_feed.reset_for_tests()
    reset_order_states_for_tests()


def _intent(**overrides) -> dict:
    base = dict(INTENT)
    base.update(
        {
            "signal_id": "sig-chase",
            "side": "BUY",
            "quantity": 5,
            "order_class": "ENTRY",
            "order_type": "MARKET",
            "ltp": 100.0,
            "limit_price": 100.0,
        }
    )
    base.update(overrides)
    return base


def _enable_chase(monkeypatch):
    from app import chase, dispatcher

    monkeypatch.setattr(dispatcher.settings, "KOTAK_CHASE_ENABLED", True, raising=False)
    monkeypatch.setattr(chase.settings, "KOTAK_CHASE_POLL_SEC", 1.0, raising=False)
    monkeypatch.setattr(chase.settings, "KOTAK_CHASE_MODIFY_SEC", 3.0, raising=False)
    monkeypatch.setattr(chase.settings, "KOTAK_ENTRY_CHASE_TICKS", 1, raising=False)
    monkeypatch.setattr(chase.settings, "KOTAK_EXIT_CHASE_TICKS", 1, raising=False)
    monkeypatch.setattr(chase.settings, "KOTAK_ENTRY_MAX_SLIP_PCT", 0.10, raising=False)
    monkeypatch.setattr(chase.settings, "KOTAK_LTP_REFRESH_ENABLED", True, raising=False)


def _install_clock(monkeypatch) -> _Clock:
    from app import chase

    clock = _Clock()
    monkeypatch.setattr(chase.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(chase.time, "sleep", clock.sleep)
    return clock


def _script_feed(monkeypatch, status_fn):
    """Each poll slice advances the fake clock, then returns a Neo frame."""
    from app import chase

    def _await(order_id, poll):
        chase.time.sleep(poll)
        return status_fn(order_id)

    monkeypatch.setattr(chase, "_await_status", _await)


def _frame(order_id: str, status: str, filled: int = 0, qty: int = 5, avg: float = 0.0, price: float = 0.0) -> dict:
    return {
        "nOrdNo": order_id,
        "ordSt": status,
        "fldQty": filled,
        "qty": qty,
        "avgPrc": avg,
        "prc": price,
    }


def test_config_defaults_leave_exit_uncapped_by_four_attempts():
    assert Settings.model_fields["KOTAK_CHASE_ENABLED"].default is True
    assert Settings.model_fields["KOTAK_CHASE_POLL_SEC"].default == 1.0
    assert Settings.model_fields["KOTAK_CHASE_MODIFY_SEC"].default == 3.0
    assert Settings.model_fields["KOTAK_ENTRY_MAX_SLIP_PCT"].default == 0.10
    assert Settings.model_fields["KOTAK_EXIT_MAX_MODIFY_ATTEMPTS"].default == 20
    assert Settings.model_fields["FEED_WAIT_SEC"].default == 8.0


def test_entry_band_and_exit_uncapped_prices():
    from app import chase

    entry = _intent(side="BUY", order_class="ENTRY", entry_max_slip_pct=0.50)
    assert chase.entry_slip(entry) == pytest.approx(0.10)
    assert chase.entry_limit_price(entry, 100.0, 130.0) == pytest.approx(110.0)

    sell_entry = _intent(side="SELL", order_class="ENTRY")
    assert chase.entry_limit_price(sell_entry, 100.0, 50.0) == pytest.approx(90.0)

    exit_sell = _intent(side="SELL", order_class="EXIT")
    px = chase.exit_limit_price(exit_sell, 80.0)
    assert px == pytest.approx(79.95)
    assert px < 100.0 * (1.0 - 0.03)
    assert px < 100.0 * (1.0 - 0.10)


def test_partial_three_of_five_modifies_same_order_then_publishes_three(monkeypatch):
    from app import chase

    _enable_chase(monkeypatch)
    _install_clock(monkeypatch)
    monkeypatch.setattr(chase.settings, "KOTAK_ENTRY_CHASE_MAX_SEC", 60.0, raising=False)

    modifies: list[dict] = []

    def _status(order_id):
        if modifies:
            return _frame(order_id, "cancelled", filled=3, avg=101.0, price=110.0)
        return _frame(order_id, "open", filled=3, price=100.0)

    def _modify(order_id, *, price, quantity, order_type="L", validity="DAY"):
        modifies.append({"order_id": order_id, "quantity": quantity, "price": price, "validity": validity})
        return {"ok": True, "order_id": order_id}

    _script_feed(monkeypatch, _status)
    monkeypatch.setattr(chase.broker_mod.broker, "modify_order", _modify)
    monkeypatch.setattr(chase.broker_mod.broker, "cancel_order", MagicMock())
    monkeypatch.setattr(chase, "read_live_ltp", lambda *_a, **_k: 130.0)

    result = chase.chase_working_order(_intent(), "OID-5", 5, 100.0)

    assert modifies, "expected a modify while the partial was still open"
    assert modifies[0]["order_id"] == "OID-5"
    assert modifies[0]["quantity"] == 5
    assert modifies[0]["price"] == pytest.approx(110.0)
    assert all(row["price"] <= 110.0 for row in modifies)
    chase.broker_mod.broker.cancel_order.assert_not_called()
    assert result["status"] == "FILLED"
    assert result["fill_type"] == "PARTIAL"
    assert result["quantity"] == 3
    assert result["order_id"] == "OID-5"


def test_entry_stops_at_band_and_budget_cancels_once(monkeypatch):
    from app import chase

    _enable_chase(monkeypatch)
    _install_clock(monkeypatch)
    monkeypatch.setattr(chase.settings, "KOTAK_ENTRY_CHASE_MAX_SEC", 12.0, raising=False)

    prices: list[float] = []

    def _modify(order_id, *, price, quantity, order_type="L", validity="DAY"):
        prices.append(price)
        return {"ok": True, "order_id": order_id}

    _script_feed(monkeypatch, lambda order_id: _frame(order_id, "open", price=prices[-1] if prices else 100.0))
    monkeypatch.setattr(chase.broker_mod.broker, "modify_order", _modify)
    cancel = MagicMock(return_value={"ok": True})
    monkeypatch.setattr(chase.broker_mod.broker, "cancel_order", cancel)
    monkeypatch.setattr(chase, "read_live_ltp", lambda *_a, **_k: 130.0)

    result = chase.chase_working_order(_intent(), "OID-E", 5, 100.0)

    assert prices == [pytest.approx(110.0)]
    cancel.assert_called_once_with("OID-E")
    assert result["status"] == "REJECTED"
    assert result["broker_status"] == "KOTAK_ENTRY_CHASE_EXHAUSTED"


def test_exit_modifies_past_four_attempts_then_cancels_once(monkeypatch):
    from app import chase

    _enable_chase(monkeypatch)
    _install_clock(monkeypatch)
    monkeypatch.setattr(chase.settings, "KOTAK_EXIT_MAX_MODIFY_ATTEMPTS", 6, raising=False)

    quotes = iter([100.0, 98.0, 96.0, 94.0, 92.0, 80.0, 70.0])
    calls: list[tuple] = []

    def _modify(order_id, *, price, quantity, order_type="L", validity="DAY"):
        calls.append((order_id, quantity, price))
        return {"ok": True, "order_id": order_id}

    cancel = MagicMock(return_value={"ok": True})
    _script_feed(
        monkeypatch,
        lambda order_id: _frame(order_id, "open", price=calls[-1][2] if calls else 100.0),
    )
    monkeypatch.setattr(chase.broker_mod.broker, "modify_order", _modify)
    monkeypatch.setattr(chase.broker_mod.broker, "cancel_order", cancel)
    monkeypatch.setattr(chase, "read_live_ltp", lambda *_a, **_k: next(quotes))

    result = chase.chase_working_order(_intent(order_class="EXIT", side="SELL", ltp=100.0), "OID-X", 5, 100.0)

    assert len(calls) == 6
    assert len(calls) > 4
    assert all(order_id == "OID-X" and qty == 5 for order_id, qty, _px in calls)
    assert calls[-1][2] < 100.0 * (1.0 - 0.10)
    cancel.assert_called_once_with("OID-X")
    assert result["broker_status"] == "KOTAK_EXIT_CHASE_EXHAUSTED"


def test_modify_rejection_cancels_once(monkeypatch):
    from app import chase

    _enable_chase(monkeypatch)
    _install_clock(monkeypatch)
    monkeypatch.setattr(chase.settings, "KOTAK_ENTRY_CHASE_MAX_SEC", 30.0, raising=False)

    cancel = MagicMock(return_value={"ok": True})
    _script_feed(monkeypatch, lambda order_id: _frame(order_id, "open", price=100.0))
    monkeypatch.setattr(chase.broker_mod.broker, "modify_order", lambda *a, **k: {"ok": False, "rejection_reason": "rejected"})
    monkeypatch.setattr(chase.broker_mod.broker, "cancel_order", cancel)
    monkeypatch.setattr(chase, "read_live_ltp", lambda *_a, **_k: 105.0)

    result = chase.chase_working_order(_intent(), "OID-R", 5, 100.0)

    cancel.assert_called_once_with("OID-R")
    assert result["status"] == "REJECTED"
    assert result["broker_status"] == "KOTAK_MODIFY_REJECTED"


def test_cancelled_with_traded_qty_publishes_partial_without_repost(monkeypatch):
    from app import chase

    _enable_chase(monkeypatch)
    _install_clock(monkeypatch)
    modify = MagicMock()
    cancel = MagicMock()
    _script_feed(monkeypatch, lambda order_id: _frame(order_id, "cancelled", filled=3, avg=99.0))
    monkeypatch.setattr(chase.broker_mod.broker, "modify_order", modify)
    monkeypatch.setattr(chase.broker_mod.broker, "cancel_order", cancel)

    result = chase.chase_working_order(_intent(quantity=5), "OID-C", 5, 100.0)

    modify.assert_not_called()
    cancel.assert_not_called()
    assert result["status"] == "FILLED"
    assert result["fill_type"] == "PARTIAL"
    assert result["quantity"] == 3


def test_session_expiry_on_modify_refreshes_once(monkeypatch):
    from app import chase

    _enable_chase(monkeypatch)
    _install_clock(monkeypatch)
    monkeypatch.setattr(chase.settings, "KOTAK_ENTRY_CHASE_MAX_SEC", 30.0, raising=False)

    calls = {"n": 0}

    def _modify(order_id, *, price, quantity, order_type="L", validity="DAY"):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SessionExpired("403")
        return {"ok": True, "order_id": order_id}

    def _status(order_id):
        if calls["n"] >= 2:
            return _frame(order_id, "complete", filled=5, avg=101.0)
        return _frame(order_id, "open", price=100.0)

    refresh = MagicMock()
    _script_feed(monkeypatch, _status)
    monkeypatch.setattr(chase.broker_mod.broker, "modify_order", _modify)
    monkeypatch.setattr(chase.broker_mod.broker.session, "refresh", refresh)
    monkeypatch.setattr(chase, "read_live_ltp", lambda *_a, **_k: 105.0)

    result = chase.chase_working_order(_intent(), "OID-S", 5, 100.0)

    assert refresh.call_count == 1
    assert order_feed.restart_count == 1
    assert calls["n"] == 2
    assert result["status"] == "FILLED"
    assert result["order_id"] == "OID-S"


def test_disconnected_feed_uses_order_report(monkeypatch):
    from app import chase

    order_feed.connected = False
    monkeypatch.setattr(order_feed, "wait", lambda order_id, timeout: None)
    monkeypatch.setattr(
        chase.broker_mod.broker,
        "order_report",
        lambda order_id: {"nOrdNo": order_id, "ordSt": "complete", "fldQty": 5, "qty": 5, "avgPrc": 101.25},
    )

    result = chase.chase_working_order(_intent(), "OID-REP", 5, 100.0)

    assert result["status"] == "FILLED"
    assert result["fill_price"] == pytest.approx(101.25)
    assert result["price_source"] == "AVERAGE_TRADED_PRICE"


def test_dispatch_places_once_and_does_not_repost_original_qty(monkeypatch):
    from app import chase, dispatcher

    _enable_chase(monkeypatch)
    _install_clock(monkeypatch)
    monkeypatch.setattr(chase.settings, "KOTAK_ENTRY_CHASE_MAX_SEC", 3.0, raising=False)

    posts: list[int] = []

    def _place(intent, trading_symbol):
        posts.append(int(float(intent["quantity"])))
        assert intent["validity"] == "DAY"
        assert str(intent["order_type"]).upper() == "LIMIT"
        assert trading_symbol == "SENSEX24OCT79800PE"
        return {"ok": True, "order_id": "OID-ONCE"}

    def _cancel(order_id):
        order_feed.handle(_frame(order_id, "cancelled", filled=3, qty=5, avg=100.0))
        return {"ok": True, "order_id": order_id}

    _script_feed(monkeypatch, lambda order_id: _frame(order_id, "open", price=100.0))
    monkeypatch.setattr(dispatcher.broker_mod.broker, "check_margin", lambda *a, **k: {"sufficient": True})
    monkeypatch.setattr(dispatcher.broker_mod.broker, "place_order", _place)
    monkeypatch.setattr(chase.broker_mod.broker, "modify_order", lambda *a, **k: {"ok": False, "rejection_reason": "no"})
    monkeypatch.setattr(chase.broker_mod.broker, "cancel_order", _cancel)
    monkeypatch.setattr(chase, "read_live_ltp", lambda *_a, **_k: 101.0)
    redis = _Redis()
    monkeypatch.setattr("app.inflight.get_redis", lambda: redis)

    result = dispatcher.dispatch_order(_intent())

    assert posts == [5]
    assert result["status"] == "FILLED"
    assert result["fill_type"] == "PARTIAL"
    assert result["quantity"] == 3
    assert result["order_id"] == "OID-ONCE"
    assert redis.kv == {}


def test_redelivery_resumes_inflight_order(monkeypatch):
    from app import dispatcher, inflight

    _enable_chase(monkeypatch)
    redis = _Redis()
    monkeypatch.setattr("app.inflight.get_redis", lambda: redis)
    inflight.save_inflight("sig-chase", "OID-OLD", 5, 100.0)

    place = MagicMock()
    monkeypatch.setattr(dispatcher.broker_mod.broker, "check_margin", lambda *a, **k: {"sufficient": True})
    monkeypatch.setattr(dispatcher.broker_mod.broker, "place_order", place)
    monkeypatch.setattr(
        dispatcher,
        "chase_working_order",
        lambda intent, order_id, qty, anchor: {
            "order_id": order_id,
            "status": "FILLED",
            "fill_type": "FULL",
            "quantity": qty,
            "fill_price": anchor,
            "broker_status": "NEO_COMPLETE",
            "rejection_reason": "",
            "price_source": "AVERAGE_TRADED_PRICE",
        },
    )

    result = dispatcher.dispatch_order(_intent())

    place.assert_not_called()
    assert result["order_id"] == "OID-OLD"
    assert result["status"] == "FILLED"
    assert redis.kv == {}


def test_chase_disabled_waits_once_for_eight_seconds(monkeypatch):
    from app import dispatcher

    monkeypatch.setattr(dispatcher.settings, "KOTAK_CHASE_ENABLED", False, raising=False)
    waits: list[float] = []
    original = order_feed.wait

    def _wait(order_id, timeout):
        waits.append(timeout)
        return original(order_id, timeout)

    def _place(intent, trading_symbol):
        order_feed.handle(
            {
                "nOrdNo": "42",
                "ordSt": "complete",
                "fldQty": "20",
                "qty": "20",
                "avgPrc": "9.5",
                "GuiOrdId": intent["signal_id"],
            }
        )
        return {"ok": True, "order_id": "42"}

    monkeypatch.setattr(order_feed, "wait", _wait)
    monkeypatch.setattr(dispatcher.broker_mod.broker, "check_margin", lambda *a, **k: {"sufficient": True})
    monkeypatch.setattr(dispatcher.broker_mod.broker, "place_order", _place)

    result = dispatcher.dispatch_order(dict(INTENT))

    assert waits == [8.0]
    assert result["status"] == "FILLED"
    assert result["order_id"] == "42"


def test_live_reference_reads_tick_key_not_working_limit(monkeypatch):
    from app import chase

    redis = _Redis()
    redis.kv["NIFTY-14Oct2026-24500-CE"] = json.dumps(
        {"ltp": 125.5, "security_id": 49081, "exchange_segment": "NSE_FNO"}
    )
    monkeypatch.setattr("app.market_consumer.get_redis", lambda: redis)
    monkeypatch.setattr(chase.settings, "KOTAK_LTP_REFRESH_ENABLED", True, raising=False)
    intent = _intent(
        underlying="NIFTY",
        expiry="2026-10-14",
        strike="24500",
        option_type="CE",
        security_id="49081",
        ltp=10.0,
        limit_price=10.0,
    )
    assert chase.live_reference(intent, 10.0) == 125.5

    redis.kv.clear()
    assert chase.live_reference(intent, 10.0) == 10.0


def test_inflight_roundtrip():
    from app import inflight

    redis = _Redis()
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.inflight.get_redis", lambda: redis)
        inflight.save_inflight("sig-1", "OID-9", 5, 100.5)
        loaded = inflight.load_inflight("sig-1")
        assert loaded["order_id"] == "OID-9"
        assert loaded["quantity"] == 5
        assert loaded["anchor"] == pytest.approx(100.5)
        assert inflight.inflight_key("sig-1") == "toe_kotak:inflight:sig-1"
        assert "toe_kotak:inflight:sig-1" in redis.kv
        inflight.clear_inflight("sig-1")
        assert inflight.load_inflight("sig-1") is None
