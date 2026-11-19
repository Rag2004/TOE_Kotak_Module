"""Halted Kotak intents are not placed. Inflight orders are cancelled."""
from app.consumer import _process_intent
from app.inflight import cancel_working_orders


def test_halted_intent_is_rejected_without_place(monkeypatch):
    calls = []
    monkeypatch.setattr("app.consumer.is_already_processed", lambda signal_id: False)
    monkeypatch.setattr(
        "app.consumer.dispatch_order",
        lambda intent: (_ for _ in ()).throw(AssertionError("placed")),
    )
    monkeypatch.setattr(
        "app.consumer.publish_execution",
        lambda payload, start: calls.append(payload) or payload,
    )
    monkeypatch.setattr("app.consumer.log_execution", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.consumer.mark_processed", lambda signal_id: calls.append(("mark", signal_id)))

    class Redis:
        def get(self, key):
            return "0"

        def xack(self, stream, group, msg_id):
            calls.append(("ack", msg_id))

    _process_intent(Redis(), "1-0", {"signal_id": "sig-halt", "strategy_id": "SN"})
    assert calls[0]["status"] == "REJECTED"
    assert calls[0]["rejection_reason"] == "account_disabled"
    assert ("mark", "sig-halt") in calls
    assert ("ack", "1-0") in calls


def test_cancel_working_orders_cancels_and_clears(monkeypatch):
    cancelled = []
    cleared = []
    monkeypatch.setattr(
        "app.inflight.list_inflight",
        lambda: [{"order_id": "OID-1", "signal_id": "sig-1"}],
    )

    class Broker:
        def cancel_order(self, order_id):
            cancelled.append(order_id)
            return {"ok": True}

    monkeypatch.setattr("app.broker.broker", Broker())
    monkeypatch.setattr("app.inflight.clear_inflight", lambda signal_id: cleared.append(signal_id))

    out = cancel_working_orders()
    assert out == {"cancelled": ["OID-1"], "failed": []}
    assert cancelled == ["OID-1"]
    assert cleared == ["sig-1"]
