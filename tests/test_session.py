import pytest

from app.session import KotakSession, SessionError, SessionExpired, assert_neo_environment, check_token_and_client_id, totp_code


@pytest.fixture(autouse=True)
def _no_kotak_redis(monkeypatch):
    monkeypatch.setattr("app.session._load_kotak_redis", lambda: None)

_TOKEN = "ec6a746c-e44b-455e-abf2-c13352b2fc45"
_CLIENT = "AB123"


def test_totp_is_six_digits():
    code = totp_code("JBSWY3DPEHPK3PXP", at=1_700_000_000)
    assert len(code) == 6
    assert code.isdigit()


def test_token_and_client_id_shape():
    check_token_and_client_id(_TOKEN, _CLIENT)
    with pytest.raises(SessionError, match="NEO_ACCESS_TOKEN"):
        check_token_and_client_id("not-a-token", _CLIENT)
    with pytest.raises(SessionError, match="NEO_CLIENT_ID"):
        check_token_and_client_id(_TOKEN, "UCC1")


def test_refuses_uat():
    with pytest.raises(SessionError):
        assert_neo_environment("uat")


def test_login_calls_totp_then_validate(monkeypatch):
    monkeypatch.setattr("app.session.settings.NEO_ENVIRONMENT", "prod")
    monkeypatch.setattr("app.session.settings.NEO_CONSUMER_KEY", _TOKEN)
    monkeypatch.setattr("app.session.settings.NEO_MOBILE_NUMBER", "9999999999")
    monkeypatch.setattr("app.session.settings.NEO_UCC", _CLIENT)
    monkeypatch.setattr("app.session.settings.NEO_MPIN", "1234")
    monkeypatch.setattr("app.session.settings.NEO_TOTP", "111111")
    monkeypatch.setattr("app.session.settings.NEO_TOTP_SECRET", "")
    calls = []

    class Client:
        def totp_login(self, **kwargs):
            calls.append(("login", kwargs["totp"], kwargs["mobile_number"]))
            return {"stat": "Ok"}

        def totp_validate(self, **kwargs):
            calls.append(("validate", kwargs["mpin"]))
            return {"data": {"status": "success", "token": "trade", "sid": "sid1"}}

    session = KotakSession(client_factory=lambda environment: Client())
    session.login()
    assert calls == [("login", "111111", "+919999999999"), ("validate", "1234")]
    assert session.authenticated


@pytest.mark.parametrize(
    ("blank", "label"),
    [("NEO_CONSUMER_KEY", "NEO_ACCESS_TOKEN"), ("NEO_UCC", "NEO_CLIENT_ID")],
)
def test_missing_token_or_client_id_skips_totp(monkeypatch, blank, label):
    monkeypatch.setattr("app.session.settings.NEO_ENVIRONMENT", "prod")
    monkeypatch.setattr("app.session.settings.NEO_CONSUMER_KEY", _TOKEN)
    monkeypatch.setattr("app.session.settings.NEO_MOBILE_NUMBER", "9999999999")
    monkeypatch.setattr("app.session.settings.NEO_UCC", _CLIENT)
    monkeypatch.setattr("app.session.settings.NEO_MPIN", "1234")
    monkeypatch.setattr("app.session.settings.NEO_TOTP", "111111")
    monkeypatch.setattr(f"app.session.settings.{blank}", "")
    calls = []

    def totp_must_not_run():
        calls.append("totp")
        raise AssertionError("current_totp should not run")

    monkeypatch.setattr("app.session.current_totp", totp_must_not_run)

    class Client:
        def totp_login(self, **kwargs):
            calls.append("login")
            return {"stat": "Ok"}

        def totp_validate(self, **kwargs):
            calls.append("validate")
            return {"stat": "Ok"}

    session = KotakSession(client_factory=lambda environment: Client())
    with pytest.raises(SessionError, match=label):
        session.login()
    assert calls == []


def test_login_prefers_redis_secret_over_env_totp(monkeypatch):
    monkeypatch.setattr("app.session.settings.NEO_ENVIRONMENT", "prod")
    monkeypatch.setattr("app.session.settings.NEO_CONSUMER_KEY", "env-token")
    monkeypatch.setattr("app.session.settings.NEO_MOBILE_NUMBER", "000")
    monkeypatch.setattr("app.session.settings.NEO_UCC", "ZZZZZ")
    monkeypatch.setattr("app.session.settings.NEO_MPIN", "0000")
    monkeypatch.setattr("app.session.settings.NEO_TOTP", "999999")
    monkeypatch.setattr(
        "app.session._load_kotak_redis",
        lambda: {
            "access_token": _TOKEN,
            "client_id": _CLIENT,
            "mobile": "9999999999",
            "mpin": "1234",
            "totp_secret": "JBSWY3DPEHPK3PXP",
        },
    )
    calls = []

    class Client:
        def totp_login(self, **kwargs):
            calls.append(kwargs)
            return {"stat": "Ok"}

        def totp_validate(self, **kwargs):
            calls.append(kwargs["mpin"])
            return {"data": {"status": "success", "token": "trade", "sid": "sid1"}}

    session = KotakSession(client_factory=lambda environment: Client())
    session.login()
    assert calls[0]["totp"] != "999999"
    assert calls[0]["totp"] == totp_code("JBSWY3DPEHPK3PXP")
    assert calls[0]["mobile_number"] == "+919999999999"
    assert calls[1] == "1234"


def test_empty_validate_is_rejected(monkeypatch):
    monkeypatch.setattr("app.session.settings.NEO_ENVIRONMENT", "prod")
    monkeypatch.setattr("app.session.settings.NEO_CONSUMER_KEY", _TOKEN)
    monkeypatch.setattr("app.session.settings.NEO_MOBILE_NUMBER", "+919999999999")
    monkeypatch.setattr("app.session.settings.NEO_UCC", _CLIENT)
    monkeypatch.setattr("app.session.settings.NEO_MPIN", "1234")
    monkeypatch.setattr("app.session.settings.NEO_TOTP", "111111")

    class Client:
        def totp_login(self, **kwargs):
            return {"stat": "Ok"}

        def totp_validate(self, **kwargs):
            return {}

    session = KotakSession(client_factory=lambda environment: Client())
    with pytest.raises(SessionError, match="session"):
        session.login()
    assert session.authenticated is False


def test_validate_403_is_session_expired(monkeypatch):
    monkeypatch.setattr("app.session.settings.NEO_ENVIRONMENT", "prod")
    monkeypatch.setattr("app.session.settings.NEO_CONSUMER_KEY", _TOKEN)
    monkeypatch.setattr("app.session.settings.NEO_MOBILE_NUMBER", "9999999999")
    monkeypatch.setattr("app.session.settings.NEO_UCC", _CLIENT)
    monkeypatch.setattr("app.session.settings.NEO_MPIN", "1234")
    monkeypatch.setattr("app.session.settings.NEO_TOTP", "111111")

    class Client:
        def totp_login(self, **kwargs):
            return {"stat": "Ok"}

        def totp_validate(self, **kwargs):
            return {"status_code": 403, "errMsg": "invalid session"}

    session = KotakSession(client_factory=lambda environment: Client())
    with pytest.raises(SessionExpired):
        session.login()
