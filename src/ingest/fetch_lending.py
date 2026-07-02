"""Fetch securities lending (借券) daily activity (free tier)."""
from __future__ import annotations

from src.ingest.finmind_client import get_loader


def fetch(stock_id: str, start_date: str, end_date: str) -> list[dict]:
    loader = get_loader()
    df = loader.taiwan_stock_securities_lending(stock_id=stock_id, start_date=start_date, end_date=end_date)
    if df.empty:
        return []

    by_date: dict[str, dict] = {}
    for _, r in df.iterrows():
        key = r["date"]
        row = by_date.setdefault(key, {
            "stock_id": r["stock_id"], "date": key,
            "lending_balance": 0, "lending_sell": 0,
        })
        volume = int(r["volume"])
        if r["transaction_type"] == "Sell":
            row["lending_sell"] += volume
        row["lending_balance"] += volume
    return list(by_date.values())
