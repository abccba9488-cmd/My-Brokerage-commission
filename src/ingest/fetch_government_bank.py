"""Fetch 八大行庫 (government-linked bank) net buy/sell, market-wide, one day
at a time (the FinMind endpoint takes no stock filter). One call here covers
every stock in the watchlist for that day — cheaper than the per-stock broker
fetch, since it's a single request regardless of watchlist size.

Requires FinMind Sponsor; degrades gracefully like fetch_broker.py.
"""
from __future__ import annotations

import logging

from src.ingest.finmind_client import get_loader, has_sponsor_token

logger = logging.getLogger(__name__)


def fetch(date: str) -> list[dict]:
    if not has_sponsor_token():
        logger.warning("FINMIND_TOKEN not set — skipping government bank data for %s", date)
        return []

    loader = get_loader()
    try:
        df = loader.taiwan_stock_government_bank_buy_sell(start_date=date)
    except Exception as exc:
        logger.warning("Government bank fetch failed for %s: %s", date, exc)
        return []

    if df.empty:
        return []

    df["net"] = df["buy"] - df["sell"]
    agg = df.groupby("stock_id", as_index=False)["net"].sum()
    return [
        {"stock_id": r["stock_id"], "date": date, "net_shares": int(r["net"])}
        for _, r in agg.iterrows()
    ]
