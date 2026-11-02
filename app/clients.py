"""Process-level Redis and Mongo clients."""
from __future__ import annotations

import threading

import redis as redis_lib
from pymongo import MongoClient

from app.config import settings

_lock = threading.Lock()
_redis: redis_lib.Redis | None = None
_mongo: MongoClient | None = None


def get_redis() -> redis_lib.Redis:
    global _redis
    if _redis is None:
        with _lock:
            if _redis is None:
                _redis = redis_lib.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis


def get_mongo_client() -> MongoClient:
    global _mongo
    if _mongo is None:
        with _lock:
            if _mongo is None:
                _mongo = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000)
    return _mongo


def reset_clients_for_tests() -> None:
    global _redis, _mongo
    with _lock:
        if _mongo is not None:
            try:
                _mongo.close()
            except Exception:
                pass
        _redis = None
        _mongo = None
