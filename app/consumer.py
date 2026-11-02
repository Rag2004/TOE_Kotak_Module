"""Read stream:order_intents_kotak_sandbox and dispatch each intent once."""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import redis as redis_lib

from app.audit import log_execution
from app.config import settings
from app.dispatcher import dispatch_order
from app.idempotency import is_already_processed, mark_processed
from app.publisher import publish_execution

logger = logging.getLogger(__name__)

_tls = threading.local()


def _execution_publish_merge(result: dict, intent_data: dict) -> dict:
    out = dict(result)
    for src, dest in (("limit_price", "limit_price"), ("ltp", "reference_ltp"), ("underlying", "underlying")):
        value = intent_data.get(src)
        if value not in (None, ""):
            out[dest] = str(value).strip()
    for key in (
        "hedge_group_id",
        "leg_role",
        "sequence",
        "wave",
        "leg_group_id",
        "exit_type",
        "cycle_id",
        "order_tag",
    ):
        value = intent_data.get(key)
        if value not in (None, ""):
            out[key] = str(value).strip()
    return out


def _redis_for_worker() -> redis_lib.Redis:
    client = getattr(_tls, "redis", None)
    if client is None:
        client = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
        _tls.redis = client
    return client


def _parallel_batch_workers(batch_len: int) -> int:
    return max(1, min(max(1, int(settings.MAX_PARALLEL_ORDER_WORKERS)), batch_len))


def _process_intent_batch(redis_main: redis_lib.Redis, stream_messages: list) -> None:
    items = list(stream_messages)
    if not items:
        return
    workers = _parallel_batch_workers(len(items))
    if workers <= 1:
        for msg_id, intent_data in items:
            _process_intent(redis_main, msg_id, intent_data)
        return

    def _run(item: tuple) -> None:
        msg_id, intent_data = item
        _process_intent(_redis_for_worker(), msg_id, intent_data)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(_run, items))


def create_consumer_group(redis_client: redis_lib.Redis) -> None:
    try:
        redis_client.xgroup_create(
            settings.STREAM_ORDER_INTENTS,
            settings.TOE_CONSUMER_GROUP,
            id="0",
            mkstream=True,
        )
        logger.info(
            "Created consumer group '%s' on '%s'",
            settings.TOE_CONSUMER_GROUP,
            settings.STREAM_ORDER_INTENTS,
        )
    except redis_lib.exceptions.ResponseError as exc:
        if "BUSYGROUP" in str(exc):
            logger.info("Consumer group '%s' already exists", settings.TOE_CONSUMER_GROUP)
        else:
            raise


def intent_consumer_loop(redis_client: redis_lib.Redis) -> None:
    logger.info(
        "TOE Kotak intent consumer started. Group=%s Consumer=%s Stream=%s",
        settings.TOE_CONSUMER_GROUP,
        settings.TOE_CONSUMER_NAME,
        settings.STREAM_ORDER_INTENTS,
    )
    while True:
        try:
            messages = redis_client.xreadgroup(
                groupname=settings.TOE_CONSUMER_GROUP,
                consumername=settings.TOE_CONSUMER_NAME,
                streams={settings.STREAM_ORDER_INTENTS: ">"},
                count=10,
                block=1000,
            )
            if not messages:
                continue
            for _stream_name, stream_messages in messages:
                _process_intent_batch(redis_client, stream_messages)
        except redis_lib.exceptions.ConnectionError:
            logger.error("Redis connection lost. Reconnecting in 5s...")
            time.sleep(5)
        except Exception as exc:
            logger.exception("Intent consumer error: %s", exc)
            time.sleep(1)


def _process_intent(redis_client: redis_lib.Redis, msg_id: str, intent_data: dict) -> None:
    signal_id = intent_data.get("signal_id", "unknown")
    strategy_id = intent_data.get("strategy_id", "unknown")
    logger.info("ORDER_INTENT RECEIVED signal=%s strategy=%s msg=%s", signal_id, strategy_id, msg_id)
    if is_already_processed(signal_id):
        logger.info("IDEMPOTENCY SKIP: signal=%s already processed", signal_id)
        redis_client.xack(settings.STREAM_ORDER_INTENTS, settings.TOE_CONSUMER_GROUP, msg_id)
        return

    start_time = datetime.now(timezone.utc).timestamp()
    try:
        result = dispatch_order(intent_data)
        result["status"] = str(result.get("status", "")).upper()
    except Exception as exc:
        logger.exception("Dispatch failed: %s", exc)
        result = {
            "order_id": "",
            "signal_id": signal_id,
            "strategy_id": strategy_id,
            "security_id": intent_data.get("security_id", "0"),
            "side": intent_data.get("side", ""),
            "quantity": intent_data.get("quantity", "0"),
            "fill_price": 0.0,
            "status": "REJECTED",
            "fill_type": "NONE",
            "broker_status": "DISPATCH_EXCEPTION",
            "rejection_reason": str(exc),
            "segment": intent_data.get("segment", ""),
            "strike": intent_data.get("strike", "0"),
            "option_type": intent_data.get("option_type", "CE"),
            "expiry": intent_data.get("expiry", ""),
            "order_class": intent_data.get("order_class", "ENTRY"),
            "order_tag": intent_data.get("order_tag", ""),
            "filled_at": "",
        }

    publish_payload = _execution_publish_merge(result, intent_data)
    try:
        exec_data = publish_execution(publish_payload, start_time)
    except Exception as exc:
        logger.error("Failed to publish execution: %s", exc)
        exec_data = {**result, "execution_id": "", "latency_ms": 0}

    latency_ms = int((datetime.now(timezone.utc).timestamp() - start_time) * 1000)
    log_execution(exec_data, latency_ms)
    mark_processed(signal_id)
    redis_client.xack(settings.STREAM_ORDER_INTENTS, settings.TOE_CONSUMER_GROUP, msg_id)
    logger.info(
        "ORDER COMPLETE signal=%s status=%s latency=%dms",
        signal_id,
        result.get("status"),
        latency_ms,
    )
