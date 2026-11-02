from app.consumer import _process_intent
from app.publisher import publish_execution


def test_publisher_sets_kotak_broker(monkeypatch):
    captured = {}

    class Redis:
        def xadd(self, stream, fields):
            captured["stream"] = stream
            captured["fields"] = fields
            return "1-0"

    monkeypatch.setattr("app.publisher.get_redis", lambda: Redis())
    published = publish_execution(
        {
            "order_id": "55",
            "signal_id": "sig",
            "strategy_id": "SN",
            "security_id": "7",
            "side": "BUY",
            "quantity": 20,
            "fill_price": 3.5,
            "status": "FILLED",
            "fill_type": "FULL",
            "price_source": "AVERAGE_TRADED_PRICE",
        },
        start_time=0,
    )
    assert captured["stream"] == "stream:executions"
    assert captured["fields"]["broker_id"] == "kotak_sandbox"
    assert published["broker_id"] == "kotak_sandbox"
    assert captured["fields"]["fill_price"] == "3.5"


def test_consumer_skips_processed_signal(monkeypatch):
    acks = []
    monkeypatch.setattr("app.consumer.is_already_processed", lambda signal_id: True)
    monkeypatch.setattr("app.consumer.dispatch_order", lambda intent: (_ for _ in ()).throw(AssertionError("dispatched")))

    class Redis:
        def xack(self, stream, group, msg_id):
            acks.append((stream, group, msg_id))

    _process_intent(Redis(), "9-0", {"signal_id": "sig", "strategy_id": "SN"})
    assert acks == [("stream:order_intents_kotak_sandbox", "toe_kotak_group", "9-0")]


def test_consumer_publishes_then_marks(monkeypatch):
    calls = []
    monkeypatch.setattr("app.consumer.is_already_processed", lambda signal_id: False)
    monkeypatch.setattr(
        "app.consumer.dispatch_order",
        lambda intent: {"signal_id": intent["signal_id"], "status": "FILLED", "order_id": "1", "fill_price": 1},
    )
    monkeypatch.setattr("app.consumer.publish_execution", lambda payload, start: calls.append("publish") or payload)
    monkeypatch.setattr("app.consumer.log_execution", lambda *a, **k: calls.append("audit"))
    monkeypatch.setattr("app.consumer.mark_processed", lambda signal_id: calls.append(("mark", signal_id)))

    class Redis:
        def xack(self, stream, group, msg_id):
            calls.append("ack")

    _process_intent(Redis(), "1-0", {"signal_id": "sig-2", "strategy_id": "SN", "underlying": "SENSEX"})
    assert calls == ["publish", "audit", ("mark", "sig-2"), "ack"]
