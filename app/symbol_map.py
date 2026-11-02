"""Resolve a desk intent to a Kotak pTrdSymbol from the Neo scrip master."""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date

logger = logging.getLogger(__name__)

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
    return None


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
    return text.upper()


def _option_type(raw: str) -> str:
    text = str(raw or "").strip().upper()
    if text in {"C", "CALL", "CE"}:
        return "CE"
    if text in {"P", "PUT", "PE"}:
        return "PE"
    return text


def normalize_row(row: dict) -> dict | None:
    trading_symbol = str(row.get("pTrdSymbol") or row.get("trdSym") or row.get("trading_symbol") or "").strip()
    if not trading_symbol:
        return None
    segment = neo_segment(str(row.get("pExchSeg") or row.get("exSeg") or row.get("exchange_segment") or ""))
    underlying = _underlying_name(
        str(row.get("pSymbolName") or row.get("pSymbol") or row.get("symbol") or "")
    )
    expiry = parse_expiry(row.get("pExpiryDate") or row.get("expiry") or row.get("lExpiryDate"))
    strike = parse_strike(row.get("dStrikePrice") if row.get("dStrikePrice") not in (None, "") else row.get("strike"))
    option_type = _option_type(str(row.get("pOptionType") or row.get("option_type") or ""))
    return {
        "trading_symbol": trading_symbol,
        "segment": segment,
        "underlying": underlying,
        "expiry": expiry,
        "strike": strike,
        "option_type": option_type,
    }


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

    def resolve(self, intent: dict) -> str:
        segment = neo_segment(str(intent.get("segment") or ""))
        underlying = _underlying_name(str(intent.get("underlying") or intent.get("underlying_scrip") or ""))
        expiry = parse_expiry(intent.get("expiry"))
        strike = parse_strike(intent.get("strike"))
        option_type = _option_type(str(intent.get("option_type") or ""))
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
        if len(matches) != 1:
            raise SymbolNotFound(
                f"segment={segment} underlying={underlying} expiry={expiry} strike={strike} option={option_type} matches={len(matches)}"
            )
        return matches[0]["trading_symbol"]


symbol_map = SymbolMap()
