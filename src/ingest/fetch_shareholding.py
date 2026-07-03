"""Fetch foreign ownership ratio (外資持股比例), daily.

Distinct from `institutional`'s daily buy/sell net: this tracks the
cumulative ownership level, useful for spotting a longer-term foreign
accumulation/distribution trend that a single day's net can't show.

Gated behind FinMind Sponsor like the other supplementary datasets, even
though it wasn't confirmed Sponsor-only during testing (a free-tier rate
limit made that inconclusive) — safer to be consistent than to assume.
"""
from __future__ import annotations

import logging

from src.ingest.finmind_client import get_loader, has_sponsor_token

logger = logging.getLogger(__name__)


def fetch(stock_id: str, start_date: str, end_date: str) -> list[dict]:
    if not has_sponsor_token():
        logger.warning("FINMIND_TOKEN not set — skipping foreign shareholding data for %s", stock_id)
        return []

    loader = get_loader()
    try:
        df = loader.taiwan_stock_shareholding(stock_id=stock_id, start_date=start_date, end_date=end_date)
    except Exception as exc:
        logger.warning("Foreign shareholding fetch failed for %s: %s", stock_id, exc)
        return []

    if df.empty:
        return []

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "stock_id": r["stock_id"],
            "date": r["date"],
            "foreign_shares_ratio": r["ForeignInvestmentSharesRatio"],
            "foreign_remain_ratio": r["ForeignInvestmentRemainRatio"],
        })
    return rows
