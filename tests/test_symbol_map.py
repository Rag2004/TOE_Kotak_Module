from app.symbol_map import SymbolNotFound, parse_strike, symbol_map


SENSEX_ROW = {
    "pTrdSymbol": "SENSEX24OCT79800PE",
    "pExchSeg": "bse_fo",
    "pSymbolName": "SENSEX",
    "pOptionType": "PE",
    "dStrikePrice": "7980000",
    "pExpiryDate": "28OCT2026",
}


def test_parse_strike_paise():
    assert parse_strike("7980000") == 79800
    assert parse_strike("79800") == 79800


def test_resolve_sensex_put():
    symbol_map.load_rows([SENSEX_ROW])
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


def test_symbol_miss():
    symbol_map.load_rows([SENSEX_ROW])
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
