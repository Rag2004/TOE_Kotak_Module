# TOE Kotak Module

Standalone Kotak Neo executor for the QuantEngine desk. It reads
`stream:order_intents_kotak_sandbox` and publishes `stream:executions` with
`broker_id=kotak_sandbox`.

Orders go to Neo `environment=prod` only. Fills come from the Neo order feed
(`nOrdNo`, `ordSt`, `fldQty`, `avgPrc`).

Working orders are chased in place. The executor places one DAY limit, waits on
the order feed about once a second, and every 3 seconds modifies that same
order from the live Redis contract tick. Entry limits stay inside a band around
the signal price. Exit limits follow the market. Set `KOTAK_CHASE_ENABLED=false`
to use the single `FEED_WAIT_SEC` wait instead.

## Layout

- `app/session.py` — dashboard access token and client code, then TOTP, then MPIN
- `app/broker.py` — place, modify, cancel, margin
- `app/symbol_map.py` — scrip master to `pTrdSymbol`
- `app/order_feed.py` — order updates and fills
- `app/chase.py` — in-place working-order chase
- `app/market_consumer.py` — Redis contract LTP
- `app/inflight.py` — resume a working order on redelivery
- `app/dispatcher.py` — one place, then chase
- `app/consumer.py` — Redis intent consumer
- `app/publisher.py` — execution stream
- `app/state_machine.py`
- `app/idempotency.py`

```bash
pip install -r requirements.txt
python -m pytest
python place_kotak_order_smoke.py --help
```

Copy `.env.example` to `.env` on the host Kotak allows. Do not commit it.
