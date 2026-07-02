"""Broker branch (分點) buying/selling streak detection.

Flags brokers that are consecutively net-buying (or net-selling), and
whether the daily net-buy amount is accelerating or decaying (DeepSeek's
"buy superset trend" point: a shrinking daily net-buy usually means the
branch already finished loading on day one and later days are just noise).
"""
from __future__ import annotations

import pandas as pd


def _daily_net(broker_df: pd.DataFrame) -> pd.DataFrame:
    df = broker_df.groupby(["broker_id", "broker_name", "date"], as_index=False).agg(
        buy_shares=("buy_shares", "sum"),
        sell_shares=("sell_shares", "sum"),
    )
    df["net"] = df["buy_shares"] - df["sell_shares"]
    return df.sort_values(["broker_id", "date"])


def compute(broker_df: pd.DataFrame, streak_min_days: int, allow_gap_days: int) -> pd.DataFrame:
    """Returns one row per broker_id with current streak length, direction, and trend."""
    if broker_df.empty:
        return pd.DataFrame(columns=[
            "broker_id", "broker_name", "streak_days", "direction", "trend", "total_net"
        ])

    daily = _daily_net(broker_df)
    results = []
    for broker_id, g in daily.groupby("broker_id"):
        g = g.sort_values("date")
        nets = g["net"].tolist()
        broker_name = g["broker_name"].iloc[-1]

        # Walk backwards from the most recent day, counting a streak while the
        # sign stays consistent, tolerating up to `allow_gap_days` sign flips.
        direction = 1 if nets[-1] > 0 else (-1 if nets[-1] < 0 else 0)
        streak = 0
        gaps_used = 0
        for net in reversed(nets):
            same_sign = (net > 0 and direction > 0) or (net < 0 and direction < 0)
            if same_sign:
                streak += 1
            elif gaps_used < allow_gap_days:
                gaps_used += 1
                streak += 1
            else:
                break

        # Trend of the buy/sell magnitude over the streak window (simple slope sign).
        window = nets[-streak:] if streak else []
        trend = "unknown"
        if len(window) >= 2:
            trend = "increasing" if window[-1] > window[0] else "decreasing"

        results.append({
            "broker_id": broker_id,
            "broker_name": broker_name,
            "streak_days": streak,
            "direction": "buy" if direction > 0 else ("sell" if direction < 0 else "flat"),
            "trend": trend,
            "total_net": sum(window),
        })

    out = pd.DataFrame(results)
    out = out[out["streak_days"] >= streak_min_days].sort_values("total_net", ascending=False)
    return out.reset_index(drop=True)
