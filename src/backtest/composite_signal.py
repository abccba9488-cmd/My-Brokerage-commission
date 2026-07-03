"""Roll the actual production BUY signal — entry_exit_signal.evaluate_entry(),
requiring >=4 of 5 conditions — across every historical day.

broker_signal.py backtests the raw "any broker has a buy-streak" condition in
isolation. But run_daily.py's real 🟢買進 signal is the *composite* rule
(streak + concentration + institutional sync + cost position + volume), which
is more selective and has never been backtested on its own. This answers
that gap by reconstructing, for each day, what evaluate_entry() would have
returned using only data available as of that day.

Performance note: a naive re-filter of the full (broker, date, price-level)
table for every rolling window is O(days x total_rows) — for a busy stock
that's millions of rows repeated ~240 times, intractable. Instead
broker_df is pre-aggregated once to one row per (broker, date) with a
buy-weighted average price, which is mathematically equivalent input for
broker_cost's weighted-cost formula but orders of magnitude smaller.
"""
from __future__ import annotations

import pandas as pd

from src.indicators import broker_cost, broker_streak, concentration, entry_exit_signal


def _pre_aggregate(broker_df: pd.DataFrame) -> pd.DataFrame:
    if broker_df.empty:
        return broker_df

    def agg(g: pd.DataFrame) -> pd.Series:
        total_buy = g["buy_shares"].sum()
        weighted_price = (g["buy_shares"] * g["price"]).sum() / total_buy if total_buy > 0 else g["price"].mean()
        return pd.Series({"buy_shares": total_buy, "sell_shares": g["sell_shares"].sum(), "price": weighted_price})

    out = (
        broker_df.groupby(["stock_id", "date", "broker_id", "broker_name"])
        .apply(agg, include_groups=False)
        .reset_index()
    )
    return out


def signal_dates(
    price_df: pd.DataFrame,
    inst_df: pd.DataFrame,
    broker_df: pd.DataFrame,
    lookback_days: int,
    config: dict,
) -> set[str]:
    price_df = price_df.sort_values("date").reset_index(drop=True)
    broker_cfg = config["broker"]
    broker_available = not broker_df.empty

    agg_broker = _pre_aggregate(broker_df)
    broker_by_date = {d: g for d, g in agg_broker.groupby("date")} if not agg_broker.empty else {}

    inst_by_date: dict[str, tuple[int, int]] = {}
    if not inst_df.empty:
        for _, row in inst_df.iterrows():
            inst_by_date[row["date"]] = (row["foreign_net"], row["trust_net"])

    dates = price_df["date"].tolist()
    closes = price_df["close"].tolist()
    volumes = price_df["volume"].tolist()

    signal_out: set[str] = set()

    for i in range(len(dates)):
        window_start = max(0, i - lookback_days + 1)
        window_dates = dates[window_start: i + 1]
        window_volume_total = sum(volumes[window_start: i + 1])

        window_broker = (
            pd.concat([broker_by_date[d] for d in window_dates if d in broker_by_date], ignore_index=True)
            if broker_by_date else pd.DataFrame(columns=["stock_id", "date", "broker_id", "broker_name", "buy_shares", "sell_shares", "price"])
        )
        window_price = price_df.iloc[window_start: i + 1]
        if not window_broker.empty:
            window_broker = broker_streak.filter_by_volume_share(
                window_broker, window_price, broker_cfg["volume_share_min_pct"]
            )

        streaks = broker_streak.compute(window_broker, broker_cfg["streak_min_days"], broker_cfg["streak_allow_gap_days"])
        top_streak = streaks.iloc[0] if not streaks.empty else None

        broker_direction, broker_streak_days, csi, pnl_pct = "none", 0, 0.0, None
        if top_streak is not None and top_streak["direction"] == "buy":
            broker_direction = "buy"
            broker_streak_days = int(top_streak["streak_days"])
            cost = broker_cost.estimate_cost(window_broker, broker_ids=[top_streak["broker_id"]])
            pnl_pct = broker_cost.profit_status(cost, closes[i])["pnl_pct"]
            csi = concentration.compute_csi(window_broker, window_volume_total, broker_cfg["top_n_concentration"])

        foreign_net, trust_net = inst_by_date.get(dates[i], (0, 0))
        avg_volume = window_volume_total / len(window_dates)

        entry = entry_exit_signal.evaluate_entry(
            broker_available=broker_available,
            broker_streak_days=broker_streak_days,
            broker_direction=broker_direction,
            csi=csi,
            foreign_net=foreign_net,
            trust_net=trust_net,
            pnl_pct=pnl_pct,
            volume=volumes[i],
            avg_volume=avg_volume,
            config=config,
        )
        if entry["action"] == "BUY":
            signal_out.add(dates[i])

    return signal_out
