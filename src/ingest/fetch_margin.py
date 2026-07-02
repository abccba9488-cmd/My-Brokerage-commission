"""Fetch margin purchase / short sale balances (融資融券, free tier)."""
from __future__ import annotations

from src.ingest.finmind_client import get_loader


def fetch(stock_id: str, start_date: str, end_date: str) -> list[dict]:
    loader = get_loader()
    df = loader.taiwan_stock_margin_purchase_short_sale(stock_id=stock_id, start_date=start_date, end_date=end_date)
    if df.empty:
        return []
    rows = []
    for _, r in df.iterrows():
        rows.append({
            "stock_id": r["stock_id"],
            "date": r["date"],
            "margin_buy": int(r["MarginPurchaseBuy"]),
            "margin_sell": int(r["MarginPurchaseSell"]),
            "margin_balance": int(r["MarginPurchaseTodayBalance"]),
            "margin_limit": int(r["MarginPurchaseLimit"]),
            "short_buy": int(r["ShortSaleBuy"]),
            "short_sell": int(r["ShortSaleSell"]),
            "short_balance": int(r["ShortSaleTodayBalance"]),
        })
    return rows
