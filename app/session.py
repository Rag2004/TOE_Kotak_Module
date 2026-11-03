"""Kotak Neo session: dashboard access token and client code, then TOTP, then MPIN. Live (prod) only."""
from __future__ import annotations

import base64
import contextvars
import hashlib
import hmac
import json
import logging
import re
import struct
import time

from app.config import settings

logger = logging.getLogger(__name__)

_KOTAK_KEY = "broker:kotak:prod"
_neo_consumer_key: contextvars.ContextVar[str] = contextvars.ContextVar("neo_consumer_key", default="")


class SessionError(RuntimeError):
    """Login could not be completed."""


class SessionExpired(SessionError):
    """Neo returned 403. Caller should refresh and retry once."""


def totp_code(secret: str, at: float | None = None, step: int = 30) -> str:
    """RFC 6238 TOTP from a base32 secret. Used when NEO_TOTP is not set."""
    padded = secret.strip().upper().replace(" ", "")
    padded += "=" * ((8 - len(padded) % 8) % 8)
    key = base64.b32decode(padded, casefold=True)
    counter = int((time.time() if at is None else at) // step)
    digest = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % 1_000_000).zfill(6)


def current_totp() -> str:
    explicit = str(settings.NEO_TOTP or "").strip()
    if explicit:
        return explicit
    secret = str(settings.NEO_TOTP_SECRET or "").strip()
    if not secret:
        raise SessionError("NEO_TOTP or NEO_TOTP_SECRET is required")
    return totp_code(secret)


def _load_kotak_redis() -> dict | None:
    """Login material saved by the dashboard. None when the key is missing."""
    try:
        from app.clients import get_redis

        raw = get_redis().get(_KOTAK_KEY)
    except Exception as exc:
        logger.debug("kotak redis read skipped: %s", exc)
        return None
    if not raw:
        return None
    try:
        body = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return body if isinstance(body, dict) else None


def _totp_for(stored: dict) -> str:
    secret = str(stored.get("totp_secret") or "").strip()
    if secret:
        return totp_code(secret)
    return current_totp()


_ACCESS_TOKEN_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9]{5}$")


def check_token_and_client_id(access_token: str | None = None, client_id: str | None = None) -> None:
    """Require a dashboard access token and a 5-character client code. Does not log either value."""
    token = str(settings.NEO_CONSUMER_KEY if access_token is None else access_token).strip()
    client = str(settings.NEO_UCC if client_id is None else client_id).strip()
    problems: list[str] = []
    if not token:
        problems.append("NEO_ACCESS_TOKEN is empty")
    elif not _ACCESS_TOKEN_RE.fullmatch(token):
        problems.append("NEO_ACCESS_TOKEN is not a dashboard token")
    if not client:
        problems.append("NEO_CLIENT_ID is empty")
    elif not _CLIENT_ID_RE.fullmatch(client):
        problems.append("NEO_CLIENT_ID is not a 5-character client code")
    if problems:
        raise SessionError("; ".join(problems))


def assert_neo_environment(environment: str | None = None) -> str:
    """This desk places live orders. UAT is refused so a leftover uat flag cannot send the token to d-mis."""
    env = str(environment if environment is not None else settings.NEO_ENVIRONMENT).strip().lower()
    if env != "prod":
        raise SessionError(f"TOE Kotak refuses environment={env or 'empty'}; only prod is allowed")
    return env


class KotakSession:
    """Holds the authenticated Neo client. Credentials stay on the client object."""

    def __init__(self, client_factory=None) -> None:
        self._client_factory = client_factory or _default_client_factory
        self.client = None
        self.authenticated = False

    def ensure(self) -> object:
        if self.authenticated and self.client is not None:
            return self.client
        return self.login()

    def login(self) -> object:
        stored = _load_kotak_redis() or {}
        environment = assert_neo_environment()
        access_token = str(stored.get("access_token") or settings.NEO_CONSUMER_KEY or "").strip()
        client_id = str(stored.get("client_id") or settings.NEO_UCC or "").strip()
        check_token_and_client_id(access_token, client_id)
        mobile = normalize_mobile(stored.get("mobile") or settings.NEO_MOBILE_NUMBER)
        mpin = str(stored.get("mpin") or settings.NEO_MPIN or "").strip()
        missing = [
            name
            for name, value in (
                ("NEO_MOBILE_NUMBER", mobile),
                ("NEO_MPIN", mpin),
            )
            if not value
        ]
        if missing:
            raise SessionError("missing credentials: " + ",".join(missing))
        code = _totp_for(stored)
        token_reset = _neo_consumer_key.set(access_token)
        try:
            client = self._client_factory(environment)
        finally:
            _neo_consumer_key.reset(token_reset)
        login_response = client.totp_login(
            mobile_number=mobile,
            ucc=client_id,
            totp=code,
        )
        _raise_if_rejected(login_response, "totp_login")
        validate_response = client.totp_validate(mpin=mpin)
        _raise_if_rejected(validate_response, "totp_validate")
        self.client = client
        self.authenticated = True
        logger.info("NEO_SESSION ready environment=%s", environment)
        return client

    def refresh(self) -> object:
        logger.info("NEO_SESSION refresh")
        self.authenticated = False
        self.client = None
        return self.login()

    def invalidate(self) -> None:
        self.authenticated = False
        self.client = None


def _default_client_factory(environment: str):
    from neo_api_client import NeoAPI

    # consumer_key is the dashboard access token (Authorization). Do not pass it as access_token.
    key = _neo_consumer_key.get() or str(settings.NEO_CONSUMER_KEY).strip()
    return NeoAPI(consumer_key=key, environment=environment)


def normalize_mobile(value) -> str:
    """Kotak expects the registered number with country code, for example +9198..."""
    text = str(value or "").strip().replace(" ", "").replace("-", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) == 10:
        return "+91" + digits
    if len(digits) == 12 and digits.startswith("91"):
        return "+" + digits
    return text


def _session_ready(response) -> bool:
    """True when MPIN validate returned a Neo session, not an empty body."""
    if not isinstance(response, dict):
        return False
    data = response.get("data") if isinstance(response.get("data"), dict) else response
    status = str(data.get("status") or data.get("stat") or "").lower()
    token = str(data.get("token") or data.get("Auth") or "").strip()
    sid = str(data.get("sid") or data.get("Sid") or "").strip()
    if status == "success":
        return True
    return bool(token and sid)


def _raise_if_rejected(response, step: str) -> None:
    if isinstance(response, dict):
        status = response.get("status_code") or response.get("stCode")
        if status in (403, "403"):
            raise SessionExpired(f"{step} unauthorized")
        stat = str(response.get("stat") or response.get("status") or "")
        if stat and stat.lower() not in {"ok", "success", ""} and "error" in response:
            raise SessionError(f"{step} failed")
    if step == "totp_validate" and not _session_ready(response):
        raise SessionError("totp_validate did not return a session")


session = KotakSession()
