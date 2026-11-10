from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.symbol_map import (
    SymbolNotFound,
    _unwrap_search_rows,
    neo_segment,
    normalize_row,
    parse_expiry,
    parse_strike,
    symbol_map,
)

IST = ZoneInfo("Asia/Kolkata")


def test_neo_segment_maps_dhan_idx_i():
    assert neo_segment("idx_i") == "nse_fo"
    assert neo_segment("IDX_I") == "nse_fo"
    assert neo_segment("NSE_FNO") == "nse_fo"


SENSEX_ROW = {
    "pTrdSymbol": "SENSEX24OCT79800PE",
    "pSymbol": "1134493",
    "pExchSeg": "bse_fo",
    "pSymbolName": "SENSEX",
    "pOptionType": "PE",
    "dStrikePrice": "7980000",
    "pExpiryDate": "28OCT2026",
}

NIFTY_CE_ROW = {
    "pTrdSymbol": "NIFTY09NOV2525300CE",
    "pSymbol": "40047",
    "pExchSeg": "nse_fo",
    "pSymbolName": "NIFTY",
    "pOptionType": "CE",
    "dStrikePrice": "2530000",
    # Neo stores IST calendar midnight as unix; UTC would wrongly become 2026-11-08.
    "lExpiryDate": str(int(datetime(2026, 11, 9, 0, 0, tzinfo=IST).timestamp())),
}


def test_parse_strike_paise():
    assert parse_strike("7980000") == 79800
    assert parse_strike("79800") == 79800
    assert parse_strike("8.17e+06") == 81700


def test_parse_expiry_formats():
    assert parse_expiry("2026-11-05") == date(2026, 11, 5)
    assert parse_expiry("05Nov2026") == date(2026, 11, 5)
    assert parse_expiry("1793903399") == date(2026, 11, 5)
    ist_midnight = str(int(datetime(2026, 11, 9, 0, 0, tzinfo=IST).timestamp()))
    assert parse_expiry(ist_midnight) == date(2026, 11, 9)


def test_normalize_semicolon_strike_header():
    row = normalize_row(
        {
            "pTrdSymbol": "SENSEX26N0578800CE",
            "pExchSeg": "bse_fo",
            "pSymbolName": "SENSEX",
            "pOptionType": "ce",
            "dStrikePrice;": "7880000.0",
            "pExpiryDate": "05Nov2026",
        }
    )
    assert row["strike"] == 78800.0
    assert row["expiry"] == date(2026, 11, 5)
    assert row["option_type"] == "CE"


def test_normalize_keeps_instrument_token():
    row = normalize_row(SENSEX_ROW)
    assert row["instrument_token"] == "1134493"
    assert row["underlying"] == "SENSEX"


def test_resolve_sensex_put():
    symbol_map.load_rows([SENSEX_ROW])
    with patch.object(symbol_map, "_search_neo", return_value=None):
        symbol = symbol_map.resolve(
            {
                "segment": "BSE_FNO",
                "underlying": "SENSEX",
                "expiry": "2026-10-28",
                "strike": "79800",
                "option_type": "PE",
            }
        )
    assert symbol == "SENSEX24OCT79800PE"


def test_resolve_underlying_scrip_id():
    symbol_map.load_rows([SENSEX_ROW])
    with patch.object(symbol_map, "_search_neo", return_value=None):
        symbol = symbol_map.resolve(
            {
                "segment": "BSE_FNO",
                "underlying_scrip": "15",
                "expiry": "2026-10-28",
                "strike": "79800",
                "option_type": "PE",
            }
        )
    assert symbol == "SENSEX24OCT79800PE"


def test_symbol_miss():
    symbol_map.load_rows([SENSEX_ROW])
    with patch.object(symbol_map, "_search_neo", return_value=None):
        try:
            symbol_map.resolve(
                {
                    "segment": "BSE_FNO",
                    "underlying": "SENSEX",
                    "expiry": "2026-10-28",
                    "strike": "79000",
                    "option_type": "PE",
                }
            )
        except SymbolNotFound:
            return
    raise AssertionError("expected SymbolNotFound")


def test_resolve_nifty_nse_fo_ist_expiry():
    symbol_map.load_rows([NIFTY_CE_ROW])
    with patch.object(symbol_map, "_search_neo", return_value=None):
        symbol = symbol_map.resolve(
            {
                "segment": "nse_fo",
                "underlying": "NIFTY",
                "expiry": "2026-11-09",
                "strike": "25300",
                "option_type": "CE",
            }
        )
    assert symbol == "NIFTY09NOV2525300CE"
    row = normalize_row(NIFTY_CE_ROW)
    assert row["expiry"] == date(2026, 11, 9)
    assert row["strike"] == 25300.0


def test_unwrap_search_scrip_dict():
    rows = _unwrap_search_rows({"data": [NIFTY_CE_ROW]})
    assert rows == [NIFTY_CE_ROW]
    assert _unwrap_search_rows([NIFTY_CE_ROW]) == [NIFTY_CE_ROW]
