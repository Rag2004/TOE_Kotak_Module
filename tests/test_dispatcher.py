import pytest

from app.dispatcher import dispatch_order
from app.order_feed import order_feed
from app.session import SessionExpired
from app.state_machine import reset_order_states_for_tests
from app.symbol_map import symbol_map
from tests.test_symbol_map import SENSEX_ROW


INTENT = {
    "signal_id": "sig-uat",
    "strategy_id": "SN_275_T1",
    "security_id": "99001",
    "side": "BUY",
    "quantity": "20",
    "order_type": "LIMIT",
    "limit_price": "12.5",
    "segment": "BSE_FNO",
    "underlying": "SENSEX",
    "expiry": "2026-10-28",
    "strike": "79800",
    "option_type": "PE",
    "product_type": "INTRADAY",
    "validity": "DAY",
    "order_class": "ENTRY",
}


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
    redis = _Redis()
    monkeypatch.setattr("app.inflight.get_redis", lambda: redis)
    monkeypatch.setattr("app.chase.broker_mod.broker.order_report", lambda order_id: None)
    yield
    order_feed.reset_for_tests()


def test_uat_place_and_feed_fill(monkeypatch):
    def place(intent, trading_symbol):
        assert trading_symbol == "SENSEX24OCT79800PE"
        order_feed.handle(
            {
                "nOrdNo": "8841",
                "ordSt": "complete",
                "fldQty": "20",
                "qty": "20",
                "avgPrc": "12.15",
                "GuiOrdId": intent["signal_id"],
            }
        )
        return {"ok": True, "order_id": "8841"}

    monkeypatch.setattr("app.dispatcher.broker_mod.broker.check_margin", lambda *a, **k: {"sufficient": True})
    monkeypatch.setattr("app.dispatcher.broker_mod.broker.place_order", place)
    result = dispatch_order(dict(INTENT))
    assert result["status"] == "FILLED"
    assert result["order_id"] == "8841"
    assert result["fill_price"] == 12.15
    assert result["security_id"] == "99001"
    assert result["price_source"] == "AVERAGE_TRADED_PRICE"
    assert result["quantity"] == 20


def test_rejected_frame(monkeypatch):
    def place(intent, trading_symbol):
        order_feed.handle(
            {"nOrdNo": "10", "ordSt": "rejected", "fldQty": "0", "qty": "20", "avgPrc": "0", "GuiOrdId": intent["signal_id"]}
        )
        return {"ok": True, "order_id": "10"}

    monkeypatch.setattr("app.dispatcher.broker_mod.broker.check_margin", lambda *a, **k: {"sufficient": True})
    monkeypatch.setattr("app.dispatcher.broker_mod.broker.place_order", place)
    result = dispatch_order(dict(INTENT))
    assert result["status"] == "REJECTED"
    assert result["broker_status"] == "REJECTED"


def test_symbol_miss_does_not_place(monkeypatch):
    called = {"place": 0}

    def place(*args, **kwargs):
        called["place"] += 1
        return {"ok": True, "order_id": "1"}

    monkeypatch.setattr("app.dispatcher.broker_mod.broker.place_order", place)
    intent = dict(INTENT)
    intent["strike"] = "1"
    result = dispatch_order(intent)
    assert result["broker_status"] == "SYMBOL_NOT_FOUND"
    assert called["place"] == 0


def test_session_403_refreshes_and_restarts_feed(monkeypatch):
    calls = {"place": 0, "refresh": 0}

    def place(intent, trading_symbol):
        calls["place"] += 1
        if calls["place"] == 1:
            raise SessionExpired("403")
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

    monkeypatch.setattr("app.dispatcher.broker_mod.broker.check_margin", lambda *a, **k: {"sufficient": True})
    monkeypatch.setattr("app.dispatcher.broker_mod.broker.place_order", place)
    monkeypatch.setattr(
        "app.dispatcher.broker_mod.broker.session.refresh",
        lambda: calls.__setitem__("refresh", calls["refresh"] + 1),
    )
    result = dispatch_order(dict(INTENT))
    assert calls["refresh"] == 1
    assert order_feed.restart_count == 1
    assert result["status"] == "FILLED"
    assert result["order_id"] == "42"


def test_insufficient_margin(monkeypatch):
    monkeypatch.setattr(
        "app.dispatcher.broker_mod.broker.check_margin",
        lambda *a, **k: {"sufficient": False, "required": 500.0, "available": 10.0},
    )
    result = dispatch_order(dict(INTENT))
    assert result["status"] == "REJECTED"
    assert result["broker_status"] == "INSUFFICIENT_MARGIN"
