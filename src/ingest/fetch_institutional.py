"""Fetch three-major-institutional-investors (三大法人) net buy/sell (free tier)."""
from __future__ import annotations

from src.ingest.finmind_client import get_loader

_CATEGORY_MAP = {
    "Foreign_Investor": "foreign_net",
    "Investment_Trust": "trust_net",
    "Dealer_self": "dealer_net",
    "Dealer_Hedging": "dealer_net",  # merged into dealer_net (self + hedging)
}


def fetch(stock_id: str, start_date: str, end_date: str) -> list[dict]:
    loader = get_loader()
    df = loader.taiwan_stock_institutional_investors(stock_id=stock_id, start_date=start_date, end_date=end_date)
    if df.empty:
        return []

    by_date: dict[str, dict] = {}
    for _, r in df.iterrows():
        field = _CATEGORY_MAP.get(r["name"])
        if field is None:
            continue
        key = r["date"]
        row = by_date.setdefault(key, {
            "stock_id": r["stock_id"], "date": key,
            "foreign_net": 0, "trust_net": 0, "dealer_net": 0,
        })
        row[field] += int(r["buy"]) - int(r["sell"])
    return list(by_date.values())
