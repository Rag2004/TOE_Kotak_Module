"""Download Neo scrip-master CSVs into the in-memory symbol map."""
from __future__ import annotations

import logging
import urllib.request

from app.symbol_map import symbol_map

logger = logging.getLogger(__name__)

_SEGMENTS = ("bse_fo.csv", "nse_fo.csv")


def refresh_symbol_map(client) -> int:
    """Load F&O scrip masters. Returns total rows kept in the map."""
    scrip_master = getattr(client, "scrip_master", None)
    if not callable(scrip_master):
        logger.warning("Neo client has no scrip_master()")
        return symbol_map.row_count
    try:
        payload = scrip_master()
    except Exception as exc:
        logger.warning("scrip_master failed: %s", exc)
        return symbol_map.row_count
    paths = []
    if isinstance(payload, dict):
        paths = payload.get("filesPaths") or payload.get("filePaths") or []
    rows: list[dict] = []
    import csv
    import io

    for path in paths:
        name = str(path).rsplit("/", 1)[-1].lower()
        if name not in _SEGMENTS:
            continue
        try:
            with urllib.request.urlopen(str(path), timeout=90) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(text))
            chunk = [row for row in reader if isinstance(row, dict)]
            rows.extend(chunk)
            logger.info("SCRIP_MASTER downloaded=%s rows=%d", name, len(chunk))
        except Exception as exc:
            logger.warning("SCRIP_MASTER download failed file=%s err=%s", name, exc)
    if rows:
        symbol_map.load_rows(rows)
    return symbol_map.row_count
