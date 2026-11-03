#!/usr/bin/env python3
"""Place one Kotak Neo live order, or print the payload with --dry-run.

Refuses any environment other than prod. Credentials come from the environment
or this module's .env.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.broker import broker, to_neo_order
from app.order_feed import order_feed
from app.session import SessionError, assert_neo_environment
from app.symbol_map import symbol_map


def build_intent(args: argparse.Namespace) -> dict:
    return {
        "signal_id": args.signal_id,
        "side": args.side,
        "quantity": str(args.quantity),
        "order_type": "LIMIT",
        "limit_price": str(args.price),
        "segment": args.segment,
        "underlying": args.underlying,
        "expiry": args.expiry,
        "strike": str(args.strike),
        "option_type": args.option_type,
        "product_type": "INTRADAY",
        "validity": "DAY",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kotak Neo live order smoke")
    parser.add_argument("--environment", default="prod")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scrip-json", default="")
    parser.add_argument("--segment", default="BSE_FNO")
    parser.add_argument("--underlying", default="SENSEX")
    parser.add_argument("--expiry", default="2026-10-28")
    parser.add_argument("--strike", default="79800")
    parser.add_argument("--option-type", default="PE")
    parser.add_argument("--side", default="BUY")
    parser.add_argument("--quantity", type=int, default=1)
    parser.add_argument("--price", default="1.00")
    parser.add_argument("--signal-id", default="kotak-live-smoke")
    parser.add_argument("--preflight", action="store_true")
    args = parser.parse_args(argv)

    try:
        assert_neo_environment(args.environment)
    except SessionError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.scrip_json:
        rows = json.loads(Path(args.scrip_json).read_text(encoding="utf-8"))
        symbol_map.load_rows(rows)
    intent = build_intent(args)
    if symbol_map.row_count == 0:
        print("Load a scrip master with --scrip-json before resolving a symbol", file=sys.stderr)
        return 2
    trading_symbol = symbol_map.resolve(intent)
    payload = to_neo_order(intent, trading_symbol)
    if args.dry_run:
        print(json.dumps({"environment": "prod", "trading_symbol": trading_symbol, "place_order": payload}, indent=2))
        return 0

    if args.preflight and hasattr(broker.session, "ensure"):
        client = broker.session.ensure()
        whats_my_ip = getattr(client, "whatsmyip", None)
        if callable(whats_my_ip):
            print(json.dumps(whats_my_ip()))

    placed = broker.place_order(intent, trading_symbol)
    print(json.dumps({"placed": placed.get("ok"), "order_id": placed.get("order_id"), "reason": placed.get("rejection_reason", "")}))
    if not placed.get("ok"):
        return 1
    order_feed.register(args.signal_id, placed["order_id"])
    print("Placed on UAT. Watch the order feed for ordSt/fldQty/avgPrc, then cancel if it stays open.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
