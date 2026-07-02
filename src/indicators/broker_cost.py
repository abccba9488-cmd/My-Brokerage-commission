"""Estimate the weighted-average cost basis of net-buying broker branches."""
from __future__ import annotations

import pandas as pd


def estimate_cost(broker_df: pd.DataFrame, broker_ids: list[str] | None = None) -> float | None:
    """Weighted-average buy price across the given brokers (or all, if None),
    weighted by each day's buy volume. Returns None if there's no buy volume."""
    df = broker_df if broker_ids is None else broker_df[broker_df["broker_id"].isin(broker_ids)]
    total_buy = df["buy_shares"].sum()
    if total_buy == 0:
        return None
    return float((df["buy_shares"] * df["price"]).sum() / total_buy)


def profit_status(cost: float | None, current_close: float) -> dict:
    if cost is None or cost == 0:
        return {"cost": None, "pnl_pct": None, "status": "unknown"}
    pnl_pct = (current_close - cost) / cost * 100
    status = "profit" if pnl_pct > 0 else ("underwater" if pnl_pct < 0 else "breakeven")
    return {"cost": round(cost, 2), "pnl_pct": round(pnl_pct, 2), "status": status}
