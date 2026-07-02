"""Fetch daily OHLCV price data (free tier)."""
from __future__ import annotations

from src.ingest.finmind_client import get_loader


def fetch(stock_id: str, start_date: str, end_date: str) -> list[dict]:
    loader = get_loader()
    df = loader.taiwan_stock_daily(stock_id=stock_id, start_date=start_date, end_date=end_date)
    if df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "stock_id": r["stock_id"],
            "date": r["date"],
            "open": r["open"],
            "high": r["max"],
            "low": r["min"],
            "close": r["close"],
            "volume": int(r["Trading_Volume"]),
        })
    return rows
