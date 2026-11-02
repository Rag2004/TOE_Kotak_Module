"""Persist each execution to Mongo order_executions."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.clients import get_mongo_client
from app.config import settings

logger = logging.getLogger(__name__)


def log_execution(result: dict, latency_ms: int = 0) -> None:
    doc = {
        "execution_id": result.get("execution_id", ""),
        "order_id": result.get("order_id", ""),
        "signal_id": result.get("signal_id", ""),
        "strategy_id": result.get("strategy_id", ""),
        "broker": "kotak_sandbox",
        "security_id": result.get("security_id"),
        "side": result.get("side", ""),
        "quantity": result.get("quantity", 0),
        "fill_price": result.get("fill_price", 0),
        "status": result.get("status", ""),
        "broker_status": result.get("broker_status", ""),
        "rejection_reason": result.get("rejection_reason", ""),
        "latency_ms": latency_ms,
        "ts": datetime.now(timezone.utc),
    }
    try:
        get_mongo_client()[settings.MONGO_DB_NAME]["order_executions"].insert_one(doc)
    except Exception as exc:
        logger.error("Audit log write failed: %s", exc)
