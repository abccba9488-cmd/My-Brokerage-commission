"""Forward-return backtest for a boolean signal series.

This is the module that actually answers "does this signal work" instead of
letting a rule-based score speak for itself. Any indicator in src/indicators
can be turned into a signal_dates list and run through here before it's
trusted.
"""
from __future__ import annotations

import pandas as pd


def run(price_df: pd.DataFrame, signal_dates: set[str], holding_days_list: list[int]) -> dict:
    """price_df: columns date, close, sorted ascending. signal_dates: set of
    'date' strings on which the signal fired. Returns per-holding-period stats.
    """
    df = price_df.sort_values("date").reset_index(drop=True)
    date_to_idx = {d: i for i, d in enumerate(df["date"])}

    results = {}
    for holding in holding_days_list:
        trades = []
        for sig_date in sorted(signal_dates):
            if sig_date not in date_to_idx:
                continue
            entry_idx = date_to_idx[sig_date]
            exit_idx = entry_idx + holding
            if exit_idx >= len(df):
                continue  # not enough forward data yet

            entry_close = df["close"].iloc[entry_idx]
            exit_close = df["close"].iloc[exit_idx]
            path = df["close"].iloc[entry_idx: exit_idx + 1]

            ret_pct = (exit_close / entry_close - 1) * 100
            max_drawdown_pct = ((path.cummax() - path) / path.cummax() * 100).max()

            trades.append({"signal_date": sig_date, "return_pct": ret_pct, "max_drawdown_pct": max_drawdown_pct})

        if not trades:
            results[holding] = {"sample_count": 0}
            continue

        trades_df = pd.DataFrame(trades)
        results[holding] = {
            "sample_count": len(trades_df),
            "win_rate_pct": round((trades_df["return_pct"] > 0).mean() * 100, 1),
            "avg_return_pct": round(trades_df["return_pct"].mean(), 2),
            "median_return_pct": round(trades_df["return_pct"].median(), 2),
            "max_drawdown_pct": round(trades_df["max_drawdown_pct"].max(), 2),
        }

    return results


def signal_from_institutional_streak(institutional_df: pd.DataFrame, min_streak_days: int) -> set[str]:
    """Example free-tier signal: date on which foreign+trust combined net buy
    has been positive for >= min_streak_days consecutive trading days."""
    df = institutional_df.sort_values("date").reset_index(drop=True)
    df["combined_net"] = df["foreign_net"] + df["trust_net"]

    signal_dates = set()
    streak = 0
    for _, row in df.iterrows():
        if row["combined_net"] > 0:
            streak += 1
        else:
            streak = 0
        if streak >= min_streak_days:
            signal_dates.add(row["date"])
    return signal_dates
