"""Publish one execution onto stream:executions for RMS."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from app.clients import get_redis
from app.config import settings

logger = logging.getLogger(__name__)


def publish_execution(result: dict, start_time: float) -> dict:
    latency_ms = int((datetime.now(timezone.utc).timestamp() - start_time) * 1000)
    execution_id = str(uuid.uuid4())
    exec_data = {
        "execution_id": execution_id,
        "order_id": str(result.get("order_id", "") or ""),
        "signal_id": str(result.get("signal_id", "") or ""),
        "strategy_id": str(result.get("strategy_id", "") or ""),
        "broker_id": "kotak_sandbox",
        "security_id": str(result.get("security_id", "0")),
        "side": str(result.get("side", "") or ""),
        "quantity": str(result.get("quantity", 0)),
        "fill_price": str(result.get("fill_price", 0)),
        "fill_type": str(result.get("fill_type", "FULL") or "FULL"),
        "status": str(result.get("status", "") or ""),
        "broker_status": str(result.get("broker_status", "") or ""),
        "rejection_reason": str(result.get("rejection_reason", "") or ""),
        "filled_at": str(result.get("filled_at", "") or ""),
        "latency_ms": str(latency_ms),
        "underlying_scrip": str(result.get("underlying_scrip", "0") or "0"),
        "underlying": str(result.get("underlying", "") or ""),
        "limit_price": str(result.get("limit_price", "") or ""),
        "reference_ltp": str(result.get("reference_ltp", "") or ""),
        "hedge_group_id": str(result.get("hedge_group_id", "") or ""),
        "leg_role": str(result.get("leg_role", "") or ""),
        "sequence": str(result.get("sequence", "") or ""),
        "price_source": str(result.get("price_source", "") or ""),
        "raw_traded_price": str(result.get("raw_traded_price", "") or ""),
        "raw_average_traded_price": str(result.get("raw_average_traded_price", "") or ""),
        "segment": str(result.get("segment", "") or ""),
        "strike": str(result.get("strike", "0") or "0"),
        "option_type": str(result.get("option_type", "CE") or "CE"),
        "expiry": str(result.get("expiry", "") or ""),
        "order_class": str(result.get("order_class", "ENTRY") or "ENTRY"),
        "order_tag": str(result.get("order_tag", "") or ""),
        "wave": str(result.get("wave", "") or ""),
        "leg_group_id": str(result.get("leg_group_id", "") or ""),
        "exit_type": str(result.get("exit_type", "") or ""),
        "cycle_id": str(result.get("cycle_id", "") or ""),
    }
    msg_id = get_redis().xadd(settings.STREAM_EXECUTIONS, exec_data)
    logger.info(
        "EXECUTION_PUBLISHED id=%s order=%s signal=%s status=%s broker=kotak_sandbox latency=%dms",
        msg_id,
        exec_data["order_id"],
        exec_data["signal_id"],
        exec_data["status"],
        latency_ms,
    )
    exec_data["stream_id"] = msg_id
    return exec_data
