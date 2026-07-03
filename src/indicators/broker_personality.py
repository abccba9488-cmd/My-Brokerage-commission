"""Classify brokers as short-term flippers ("隔日沖客") vs longer-horizon
participants, from historical T+1 behavior: does a big net-buy day tend to
be followed by a net-sell the very next trading day?

This is the 分點性格標籤 the original multi-AI discussion asked for, done
with a simple, auditable rule (T+1 flip rate) instead of an ML embedding —
an embedding needs far more history per broker than we have to fit
reliably, and would trade away the "every number traces to a reason"
property the rest of this system is built around.
"""
from __future__ import annotations

import pandas as pd

from src.indicators.broker_streak import daily_net

FLIP_RATE_THRESHOLD = 0.6  # >=60% of big-buy days followed by a next-day sell -> flipper
MIN_BIG_BUY_DAYS = 5  # need at least this many observations before trusting the label


def classify_brokers(broker_df: pd.DataFrame, price_df: pd.DataFrame, min_share_pct: float) -> pd.DataFrame:
    """One row per broker_id with enough history: big_buy_days, flip_days,
    flip_rate, label ("隔日沖客" / "波段/其他")."""
    empty = pd.DataFrame(columns=["broker_id", "broker_name", "big_buy_days", "flip_days", "flip_rate", "label"])
    if broker_df.empty:
        return empty

    daily = daily_net(broker_df)
    volume_by_date = price_df.set_index("date")["volume"]
    daily["day_volume"] = daily["date"].map(volume_by_date)
    daily["buy_share_pct"] = daily["buy_shares"] / daily["day_volume"] * 100

    trading_dates = sorted(price_df["date"].unique())
    next_date_map = {d: trading_dates[i + 1] for i, d in enumerate(trading_dates[:-1])}
    net_by_broker_date = daily.set_index(["broker_id", "date"])["net"]

    results = []
    for broker_id, g in daily.groupby("broker_id"):
        broker_name = g["broker_name"].iloc[-1]
        big_buy_dates = g.loc[(g["net"] > 0) & (g["buy_share_pct"] >= min_share_pct), "date"]
        if len(big_buy_dates) < MIN_BIG_BUY_DAYS:
            continue

        flip_count, valid_count = 0, 0
        for d in big_buy_dates:
            next_d = next_date_map.get(d)
            key = (broker_id, next_d)
            if next_d is None or key not in net_by_broker_date.index:
                continue
            valid_count += 1
            if net_by_broker_date.loc[key] < 0:
                flip_count += 1

        if valid_count < MIN_BIG_BUY_DAYS:
            continue

        flip_rate = flip_count / valid_count
        results.append({
            "broker_id": broker_id,
            "broker_name": broker_name,
            "big_buy_days": valid_count,
            "flip_days": flip_count,
            "flip_rate": round(flip_rate, 3),
            "label": "隔日沖客" if flip_rate >= FLIP_RATE_THRESHOLD else "波段/其他",
        })

    if not results:
        return empty
    return pd.DataFrame(results).sort_values("flip_rate", ascending=False).reset_index(drop=True)
