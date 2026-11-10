"""TOE Kotak FastAPI process."""
from __future__ import annotations

import logging
import threading
import time

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings
from app.consumer import create_consumer_group, intent_consumer_loop
from app.order_feed import order_feed
from app.readiness import mark_consumers_started, set_startup_error
from app.session import SessionError, session
from app.startup import wait_for_mongo, wait_for_redis

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="TOE Kotak — Trade Order Executor (Kotak Neo UAT)", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


def _order_feed_loop() -> None:
    while True:
        try:
            client = session.ensure()
            order_feed.serve(client)
        except SessionError as exc:
            logger.warning("Order feed waiting for a live session: %s", exc)
        except Exception as exc:
            logger.warning("Order feed stopped: %s", exc)
        time.sleep(5)


def _load_scrip_master_loop() -> None:
    """Warm the symbol map once a Neo session is available."""
    from app.scrip_master import refresh_symbol_map

    while True:
        try:
            client = session.ensure()
            count = refresh_symbol_map(client)
            if count > 0:
                logger.info("TOE Kotak scrip master ready rows=%d", count)
                return
        except SessionError as exc:
            logger.warning("Scrip master waiting for a live session: %s", exc)
        except Exception as exc:
            logger.warning("Scrip master load failed: %s", exc)
        time.sleep(5)


@app.on_event("startup")
def startup() -> None:
    logger.info("TOE Kotak starting up...")
    set_startup_error(None)
    try:
        redis_client = wait_for_redis()
        wait_for_mongo()
    except Exception as exc:
        set_startup_error(str(exc))
        logger.error("TOE Kotak startup aborted: %s", exc)
        raise
    create_consumer_group(redis_client)
    threading.Thread(
        target=intent_consumer_loop,
        args=(redis_client,),
        daemon=True,
        name="toe-kotak-intent-consumer",
    ).start()
    threading.Thread(target=_order_feed_loop, daemon=True, name="toe-kotak-order-feed").start()
    threading.Thread(target=_load_scrip_master_loop, daemon=True, name="toe-kotak-scrip-master").start()
    mark_consumers_started()
    logger.info("TOE Kotak ready (Redis + Mongo + consumers)")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=settings.TOE_KOTAK_PORT)
