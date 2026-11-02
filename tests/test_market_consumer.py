"""Live tick key reader. No market_service import and no tick stream."""
import json

from app.market_consumer import contract_key, read_live_ltp


def _intent(**overrides):
    base = {
        "underlying": "NIFTY",
        "expiry": "2026-10-14",
        "strike": 24500,
        "option_type": "CE",
        "security_id": "49081",
    }
    base.update(overrides)
    return base


class _Redis:
    def __init__(self, value=None, error=None):
        self.value = value
        self.error = error
        self.keys = []

    def get(self, key):
        self.keys.append(key)
        if self.error is not None:
            raise self.error
        if isinstance(self.value, dict):
            return self.value.get(key)
        return self.value


def test_contract_key_matches_feed_name():
    assert contract_key(_intent(underlying="NIFTY 50", strike="24500.0")) == "NIFTY-14Oct2026-24500-CE"
    assert contract_key(_intent(underlying="", underlying_scrip=13, option_type="PE")) == "NIFTY-14Oct2026-24500-PE"
    assert contract_key(_intent(underlying="", underlying_scrip=15, option_type="PE")) == "SENSEX-14Oct2026-24500-PE"
    assert contract_key(_intent(underlying="", underlying_scrip=1)) == "SENSEX-14Oct2026-24500-CE"


def test_index_key_is_not_an_option_premium(monkeypatch):
    redis = _Redis(json.dumps({"ltp": 24500.0}))
    monkeypatch.setattr("app.market_consumer.get_redis", lambda: redis)
    assert contract_key({"underlying": "NIFTY"}) is None
    assert read_live_ltp({"underlying": "NIFTY", "security_id": "13"}) is None
    assert redis.keys == []


def test_matching_tick_returns_ltp(monkeypatch):
    body = json.dumps({"ltp": 125.5, "security_id": 49081, "exchange_segment": "NSE_FNO"})
    redis = _Redis(body)
    monkeypatch.setattr("app.market_consumer.get_redis", lambda: redis)
    assert read_live_ltp(_intent()) == 125.5
    assert redis.keys == ["NIFTY-14Oct2026-24500-CE"]


def test_security_id_mismatch_returns_nothing(monkeypatch):
    redis = _Redis(json.dumps({"ltp": 125.5, "security_id": 1}))
    monkeypatch.setattr("app.market_consumer.get_redis", lambda: redis)
    assert read_live_ltp(_intent()) is None


def test_redis_miss_returns_nothing(monkeypatch):
    monkeypatch.setattr("app.market_consumer.get_redis", lambda: _Redis(None))
    assert read_live_ltp(_intent()) is None


def test_redis_error_returns_nothing(monkeypatch):
    monkeypatch.setattr(
        "app.market_consumer.get_redis",
        lambda: _Redis(error=ConnectionError("down")),
    )
    assert read_live_ltp(_intent()) is None


def test_non_positive_or_bad_body_returns_nothing(monkeypatch):
    redis = _Redis(json.dumps({"ltp": 0, "security_id": 49081}))
    monkeypatch.setattr("app.market_consumer.get_redis", lambda: redis)
    assert read_live_ltp(_intent()) is None
    redis.value = "not-json"
    assert read_live_ltp(_intent()) is None


def test_reader_does_not_import_market_service():
    import app.market_consumer as consumer

    source = open(consumer.__file__, encoding="utf-8").read()
    assert "market_service" not in source
    assert "XREAD" not in source
