"""Fetch broker branch (分點) daily trading detail.

Requires a FinMind Sponsor-level token. Degrades gracefully when no token is
configured, or when the account level is insufficient: logs a warning and
returns an empty list instead of raising, so the rest of the pipeline keeps
running on free-tier data.
"""
from __future__ import annotations

import logging

from src.ingest.finmind_client import get_loader, has_sponsor_token

logger = logging.getLogger(__name__)


def fetch(stock_id: str, date: str) -> list[dict]:
    """FinMind's broker report endpoint is single-day-per-request even for Sponsor accounts."""
    if not has_sponsor_token():
        logger.warning(
            "FINMIND_TOKEN not set — skipping broker branch data for %s %s "
            "(requires FinMind Sponsor, NT$999/month). See .env.example.",
            stock_id, date,
        )
        return []

    loader = get_loader()
    try:
        df = loader.taiwan_stock_trading_daily_report(stock_id=stock_id, date=date)
    except Exception as exc:  # FinMind raises a bare Exception on insufficient plan level
        logger.warning("Broker branch fetch failed for %s %s: %s", stock_id, date, exc)
        return []

    if df.empty:
        return []

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "stock_id": r.get("stock_id", stock_id),
            "date": date,
            "broker_id": r["securities_trader_id"],
            "broker_name": r["securities_trader"],
            "buy_shares": int(r["buy"]),
            "sell_shares": int(r["sell"]),
            "price": float(r["price"]),
        })
    return rows
