"""Re-run resumes chase on the saved order and does not place another."""
from __future__ import annotations

from app.chase import reset_chase_reservations


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


class _InlineThread:
    def __init__(self, target, args=(), daemon=None, name=None):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


def _install(monkeypatch, redis):
    monkeypatch.setattr("app.inflight.get_redis", lambda: redis)
    monkeypatch.setattr("app.resume.threading.Thread", _InlineThread)
    reset_chase_reservations()


def test_resume_calls_chase_on_the_saved_order(monkeypatch):
    from app import inflight
    from app.resume import resume_working_order

    redis = _Redis()
    _install(monkeypatch, redis)
    inflight.save_inflight(
        "sig-1",
        "OID-9",
        65,
        102.15,
        security_id="45401",
        intent={
            "signal_id": "sig-1",
            "strategy_id": "NF_TGT_PEN12_T1",
            "side": "SELL",
            "order_class": "ENTRY",
            "security_id": "45401",
            "ltp": 102.15,
            "underlying": "NIFTY",
        },
    )
    seen = {}

    def _chase(intent, order_id, qty, anchor, reserved=False):
        seen["intent"] = dict(intent)
        seen["order_id"] = order_id
        seen["qty"] = qty
        seen["anchor"] = anchor
        seen["reserved"] = reserved
        return {
            "order_id": order_id,
            "status": "FILLED",
            "fill_type": "FULL",
            "quantity": qty,
            "fill_price": anchor,
            "broker_status": "NEO_COMPLETE",
            "rejection_reason": "",
            "price_source": "AVERAGE_TRADED_PRICE",
        }

    published = []
    monkeypatch.setattr("app.chase.chase_working_order", _chase)
    monkeypatch.setattr("app.resume.feed_mod.order_feed.register", lambda *a, **k: None)
    monkeypatch.setattr(
        "app.publisher.publish_execution",
        lambda payload, start: published.append(payload) or payload,
    )

    out = resume_working_order("OID-9")

    assert out["ok"] is True
    assert out["resumed"] is True
    assert out["reason"] == "WORKING"
    assert seen["order_id"] == "OID-9"
    assert seen["qty"] == 65
    assert seen["anchor"] == 102.15
    assert seen["reserved"] is True
    assert seen["intent"]["side"] == "SELL"
    assert seen["intent"]["strategy_id"] == "NF_TGT_PEN12_T1"
    assert published and published[0]["status"] == "FILLED"
    assert published[0]["order_id"] == "OID-9"
    assert inflight.load_inflight_by_order("OID-9") is None


def test_resume_does_not_start_a_second_loop(monkeypatch):
    from app import chase, inflight
    from app.resume import resume_working_order

    redis = _Redis()
    _install(monkeypatch, redis)
    inflight.save_inflight("sig-1", "OID-9", 65, 100.0, intent={"side": "BUY", "signal_id": "sig-1"})
    chase.reserve_chase("OID-9")
    monkeypatch.setattr(
        "app.chase.chase_working_order",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("second chase")),
    )

    out = resume_working_order("OID-9")

    assert out["resumed"] is False
    assert out["reason"] == "ALREADY_CHASING"


def test_resume_without_inflight_does_not_place(monkeypatch):
    from app.resume import resume_working_order

    _install(monkeypatch, _Redis())
    out = resume_working_order("OID-MISSING")
    assert out["ok"] is False
    assert out["reason"] == "NO_INFLIGHT"
    assert out["resumed"] is False
