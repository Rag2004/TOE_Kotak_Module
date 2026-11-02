from app.order_feed import OrderFeed, is_terminal_update, normalize_update


class OrderData:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


class OrderUpdate:
    def __init__(self, payload):
        self.data = OrderData(payload)

    def model_dump(self):
        return {"data": self.data.model_dump()}


def test_complete_frame_is_a_fill():
    feed = OrderFeed()
    update = feed.handle(
        OrderUpdate(
            {
                "order_id": "555",
                "order_status": "complete",
                "filled_quantity": "20",
                "quantity": "20",
                "average_price": "11.4",
                "gui_ord_id": "sig-9",
            }
        )
    )
    assert update["nOrdNo"] == "555"
    assert update["fldQty"] == 20
    assert update["avgPrc"] == 11.4
    assert is_terminal_update(update)


def test_rejected_frame_is_terminal():
    feed = OrderFeed()
    update = feed.handle({"nOrdNo": "7", "ordSt": "rejected", "fldQty": "0", "qty": "20", "GuiOrdId": "sig"})
    assert update["ordSt"] == "rejected"
    assert is_terminal_update(normalize_update(update))
