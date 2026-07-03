"""Fetch shareholder concentration by holding-size tier (股東持股分級表, weekly).

Pre-aggregates FinMind's ~15 raw tiers into major (>=400,001 shares, i.e.
400張+ — the exact threshold ChatGPT's original "籌碼集中率模型" module used)
vs retail holders, since that split is all the indicators need.

Requires FinMind Sponsor; degrades gracefully like fetch_broker.py.
"""
from __future__ import annotations

import logging

from src.ingest.finmind_client import get_loader, has_sponsor_token

logger = logging.getLogger(__name__)

_EXCLUDED_LEVELS = {"total", "差異數調整（說明4）"}
_MAJOR_HOLDER_LEVELS = {"400,001-600,000", "600,001-800,000", "800,001-1,000,000", "more than 1,000,001"}


def fetch(stock_id: str, start_date: str, end_date: str) -> list[dict]:
    if not has_sponsor_token():
        logger.warning("FINMIND_TOKEN not set — skipping holder concentration data for %s", stock_id)
        return []

    loader = get_loader()
    try:
        df = loader.taiwan_stock_holding_shares_per(stock_id=stock_id, start_date=start_date, end_date=end_date)
    except Exception as exc:
        logger.warning("Holder concentration fetch failed for %s: %s", stock_id, exc)
        return []

    if df.empty:
        return []

    df = df[~df["HoldingSharesLevel"].isin(_EXCLUDED_LEVELS)]

    rows = []
    for date, g in df.groupby("date"):
        major = g[g["HoldingSharesLevel"].isin(_MAJOR_HOLDER_LEVELS)]
        rows.append({
            "stock_id": stock_id,
            "date": date,
            "major_holder_pct": round(major["percent"].sum(), 2),
            "retail_holder_pct": round(100 - major["percent"].sum(), 2),
            "major_holder_people": int(major["people"].sum()),
            "total_people": int(g["people"].sum()),
        })
    return rows
