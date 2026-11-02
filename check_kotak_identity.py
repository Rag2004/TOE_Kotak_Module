#!/usr/bin/env python3
"""Check NEO_ACCESS_TOKEN and NEO_CLIENT_ID from .env. Does not print either value."""
from __future__ import annotations

from app.session import SessionError, check_token_and_client_id


def main() -> int:
    try:
        check_token_and_client_id()
    except SessionError as exc:
        print(exc)
        return 1
    print("NEO_ACCESS_TOKEN and NEO_CLIENT_ID look valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
