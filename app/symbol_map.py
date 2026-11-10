"""Resolve a desk intent to a Kotak pTrdSymbol from the Neo scrip master."""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
NSE_EPOCH = 315532800

_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
_MONTH_NAMES = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

SEGMENT_TO_NEO = {
    "NSE_FNO": "nse_fo",
    "NSE_FO": "nse_fo",
    "NFO": "nse_fo",
    "BSE_FNO": "bse_fo",
    "BSE_FO": "bse_fo",
    "BFO": "bse_fo",
    "NSE_EQ": "nse_cm",
    "NSE": "nse_cm",
    "BSE_EQ": "bse_cm",
    "BSE": "bse_cm",
    "NSE_CM": "nse_cm",
    "BSE_CM": "bse_cm",
    "MCX": "mcx_fo",
    "MCX_FO": "mcx_fo",
    # Dhan feed / desk intent segments → Neo exchange_segment
    "IDX_I": "nse_fo",
    "I": "nse_fo",
}

# Desk intents carry Dhan underlying_scrip ids; Neo rows use names.
_SCRIP_SYMBOLS = {
    "13": "NIFTY",
    "15": "SENSEX",
    "1": "SENSEX",
}

_UNDERLYING_ALIASES = {
    "NIFTY 50": "NIFTY",
    "NIFTY50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "BANK NIFTY": "BANKNIFTY",
}


class SymbolNotFound(LookupError):
    """No scrip-master row matched the intent."""


def neo_segment(segment: str) -> str:
    raw = str(segment or "").strip()
    if not raw:
        return ""
    key = raw.upper().replace("-", "_").replace(" ", "_")
    if key in SEGMENT_TO_NEO:
        return SEGMENT_TO_NEO[key]
    lowered = raw.lower()
    if lowered in {"nse_fo", "bse_fo", "nse_cm", "bse_cm", "mcx_fo", "nse_com"}:
        return lowered
    # Dhan lowercase feed codes (e.g. idx_i for NIFTY F&O).
    if lowered in {"idx_i", "i"}:
        return "nse_fo"
    return lowered


def parse_expiry(raw) -> date | None:
    text = str(raw or "").strip()
    if not text:
        return None
    iso = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    named = re.fullmatch(r"(\d{1,2})[- ]?([A-Za-z]{3})[- ]?(\d{4})", text)
    if named:
        month = _MONTHS.get(named.group(2).upper())
        if month:
            return date(int(named.group(3)), month, int(named.group(1)))
    try:
        ts = float(text)
    except ValueError:
        return None
    if ts > 1e12:  # milliseconds
        ts /= 1000.0
    if ts < 1e9:  # not a unix timestamp
        return None
    if ts < 1_700_000_000:
        ts += NSE_EPOCH
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(IST).date()


def parse_strike(raw) -> float | None:
    if raw in (None, ""):
        return None
    try:
        value = float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    # Neo index strikes are often in paise (7980000 -> 79800).
    if value >= 100000:
        value = value / 100.0
    return value


def _underlying_name(raw: str) -> str:
    text = str(raw or "").strip()
    if ":" in text:
        text = text.split(":")[-1]
    text = text.upper()
    text = _UNDERLYING_ALIASES.get(text, text)
    if text in _SCRIP_SYMBOLS:
        return _SCRIP_SYMBOLS[text]
    try:
        as_int = str(int(float(text)))
    except (TypeError, ValueError):
        return text
    return _SCRIP_SYMBOLS.get(as_int, text)


def _option_type(raw: str) -> str:
    text = str(raw or "").strip().upper()
    if text in {"C", "CALL", "CE"}:
        return "CE"
    if text in {"P", "PUT", "PE"}:
        return "PE"
    return text


def _cell(row: dict, *names: str):
    """Read a Neo CSV/search field. Headers may have trailing spaces or a trailing ';'."""
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
        semi = f"{name};"
        if semi in row and row[semi] not in (None, ""):
            return row[semi]
    folded = {}
    for key, value in row.items():
        norm = str(key).strip().rstrip(";").lower()
        if norm and norm not in folded and value not in (None, ""):
            folded[norm] = value
    for name in names:
        value = folded.get(str(name).strip().rstrip(";").lower())
        if value not in (None, ""):
            return value
    return None


def normalize_row(row: dict) -> dict | None:
    trading_symbol = str(
        _cell(row, "pTrdSymbol", "trdSym", "trading_symbol") or ""
    ).strip()
    if not trading_symbol:
        return None
    segment = neo_segment(str(_cell(row, "pExchSeg", "exSeg", "exchange_segment") or ""))
    underlying = _underlying_name(str(_cell(row, "pSymbolName", "symbol") or ""))
    instrument_token = str(_cell(row, "pSymbol") or "").strip()
    if instrument_token.endswith(".0"):
        instrument_token = instrument_token[:-2]
    # Prefer the human expiry string; fall back to unix lExpiryDate from Neo CSVs.
    expiry = parse_expiry(_cell(row, "pExpiryDate", "expiry")) or parse_expiry(
        _cell(row, "lExpiryDate", "lExpiryDate ")
    )
    strike = parse_strike(_cell(row, "dStrikePrice", "strike"))
    option_type = _option_type(str(_cell(row, "pOptionType", "option_type") or ""))
    return {
        "trading_symbol": trading_symbol,
        "instrument_token": instrument_token,
        "segment": segment,
        "underlying": underlying,
        "expiry": expiry,
        "strike": strike,
        "option_type": option_type,
    }


def neo_expiry_query(expiry: date) -> str:
    """Neo search_scrip expiry token, e.g. 05NOV2026."""
    return f"{expiry.day:02d}{_MONTH_NAMES[expiry.month - 1]}{expiry.year}"


class SymbolMap:
    def __init__(self) -> None:
        self._rows: list[dict] = []

    def clear(self) -> None:
        self._rows = []

    @property
    def row_count(self) -> int:
        return len(self._rows)

    def load_rows(self, rows: list[dict]) -> int:
        loaded = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized = normalize_row(row)
            if normalized:
                loaded.append(normalized)
        self._rows = loaded
        logger.info("SYMBOL_MAP loaded=%d", len(loaded))
        return len(loaded)

    def load_csv(self, text: str) -> int:
        reader = csv.DictReader(io.StringIO(text))
        return self.load_rows(list(reader))

    def _match_rows(
        self,
        *,
        segment: str,
        underlying: str,
        expiry: date | None,
        strike: float | None,
        option_type: str,
    ) -> list[dict]:
        matches = []
        for row in self._rows:
            if segment and row["segment"] != segment:
                continue
            if underlying and row["underlying"] != underlying:
                continue
            if expiry and row["expiry"] != expiry:
                continue
            if option_type and row["option_type"] and row["option_type"] != option_type:
                continue
            if strike is not None and row["strike"] is not None and abs(row["strike"] - strike) > 0.01:
                continue
            matches.append(row)
        return matches

    def _search_neo(self, intent: dict, *, segment: str, underlying: str, expiry: date | None, strike: float | None, option_type: str) -> dict | None:
        if not underlying or expiry is None or strike is None or not option_type:
            return None
        try:
            from app.session import session

            client = session.ensure()
            search = getattr(client, "search_scrip", None)
            if not callable(search):
                return None
            rows = search(
                exchange_segment=segment or "bse_fo",
                symbol=underlying,
                expiry=neo_expiry_query(expiry),
                option_type=option_type,
                strike_price=str(int(strike) if float(strike).is_integer() else strike),
            )
        except Exception as exc:
            logger.warning("Neo search_scrip failed: %s", exc)
            return None
        rows = _unwrap_search_rows(rows)
        if not rows:
            return None
        normalized = [normalize_row(row) for row in rows if isinstance(row, dict)]
        normalized = [row for row in normalized if row]
        matches = []
        for row in normalized:
            if segment and row["segment"] and row["segment"] != segment:
                continue
            if underlying and row["underlying"] != underlying:
                continue
            if expiry and row["expiry"] and row["expiry"] != expiry:
                continue
            if option_type and row["option_type"] and row["option_type"] != option_type:
                continue
            if strike is not None and row["strike"] is not None and abs(row["strike"] - strike) > 0.01:
                continue
            matches.append(row)
        if len(matches) == 1:
            return matches[0]
        # Query already scoped by expiry + strike + CE/PE; one normalized row is enough.
        if len(normalized) == 1:
            return normalized[0]
        return None

    def resolve_contract(self, intent: dict) -> dict:
        """Trading symbol plus Neo pSymbol used by margin_required."""
        segment = neo_segment(str(intent.get("segment") or ""))
        underlying = _underlying_name(str(intent.get("underlying") or intent.get("underlying_scrip") or ""))
        expiry = parse_expiry(intent.get("expiry"))
        strike = parse_strike(intent.get("strike"))
        option_type = _option_type(str(intent.get("option_type") or ""))
        matches = self._match_rows(
            segment=segment,
            underlying=underlying,
            expiry=expiry,
            strike=strike,
            option_type=option_type,
        )
        if len(matches) == 1:
            return matches[0]
        searched = self._search_neo(
            intent,
            segment=segment,
            underlying=underlying,
            expiry=expiry,
            strike=strike,
            option_type=option_type,
        )
        if searched:
            return searched
        raise SymbolNotFound(
            f"segment={segment} underlying={underlying} expiry={expiry} strike={strike} option={option_type} matches={len(matches)}"
        )

    def resolve(self, intent: dict) -> str:
        return self.resolve_contract(intent)["trading_symbol"]


def _unwrap_search_rows(payload) -> list:
    """Neo search_scrip may return a list or a dict wrapping the list."""
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "files", "result", "scrips", "list"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    for value in payload.values():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return value
    return []


symbol_map = SymbolMap()
