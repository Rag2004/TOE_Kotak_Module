from pathlib import Path

from place_kotak_order_smoke import main

FIXTURE = Path(__file__).parent / "fixtures" / "sensex_scrip.json"


def test_dry_run_prints_prod_payload(capsys):
    code = main(
        [
            "--dry-run",
            "--environment",
            "prod",
            "--scrip-json",
            str(FIXTURE),
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert '"environment": "prod"' in out
    assert "SENSEX24OCT79800PE" in out
    assert '"exchange_segment": "bse_fo"' in out


def test_smoke_refuses_uat():
    assert main(["--environment", "uat", "--dry-run"]) == 2
