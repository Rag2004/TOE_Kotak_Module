from app.idempotency import is_already_processed, mark_processed


class FakeRedis:
    def __init__(self):
        self.members = set()
        self.expire_called = False

    def sismember(self, key, member):
        assert key.startswith("toe_kotak:processed:")
        return member in self.members

    def sadd(self, key, member):
        self.members.add(member)

    def expire(self, key, ttl):
        self.expire_called = True
        assert ttl == 86400


def test_mark_then_skip(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr("app.idempotency._get_redis", lambda: redis)
    assert is_already_processed("sig-1") is False
    mark_processed("sig-1")
    assert redis.expire_called
    assert is_already_processed("sig-1") is True
