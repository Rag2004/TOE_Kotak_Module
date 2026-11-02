from app.broker import to_neo_order


def test_option_buy_maps_to_neo_fields():
    payload = to_neo_order(
        {
            "signal_id": "sig-1",
            "side": "BUY",
            "quantity": "20",
            "order_type": "LIMIT",
            "limit_price": "12.5",
            "segment": "BSE_FNO",
            "product_type": "INTRADAY",
            "validity": "DAY",
        },
        "SENSEX24OCT79800PE",
    )
    assert payload["exchange_segment"] == "bse_fo"
    assert payload["transaction_type"] == "B"
    assert payload["order_type"] == "L"
    assert payload["product"] == "MIS"
    assert payload["trading_symbol"] == "SENSEX24OCT79800PE"
    assert payload["price"] == "12.50"
    assert payload["tag"] == "sig-1"
    assert payload["quantity"] == "20"
