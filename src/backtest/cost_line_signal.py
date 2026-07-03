"""Institutional cost-line entry signal — the 法人成本線 methodology the
user described: buy when price sits just above an investor type's cost
basis (freshly built position, safest margin) while that cost line is
still rising (institutions net-buying, not distributing). Free-tier data
only (三大法人) — no FinMind Sponsor / 分點 dependency, unlike every prior
signal tested in this project.

This is a fresh, untested hypothesis. Same rigor as every other signal
here: pooled vs baseline forward returns, Mann-Whitney + FDR across the
watchlist, and split-sample stability across chronological sub-periods
(see run_walkforward_candidates.py for why the composite signal's early
"passing" grades didn't survive that check) — all before trusting it for
anything live in run_daily.py.
"""
from __future__ import annotations

import pandas as pd

from src.indicators import institutional_cost


def signal_dates(price_df: pd.DataFrame, inst_df: pd.DataFrame, lookback_days: int, config: dict) -> set[str]:
    cost_cfg = config["cost_line"]
    investor = cost_cfg["investor"]
    cost_col = f"{investor}_cost"
    dev_col = f"{investor}_deviation_pct"
    trend_window = cost_cfg["cost_trend_window_days"]

    df = institutional_cost.compute(price_df, inst_df, lookback_days)
    signals: set[str] = set()

    for i in range(trend_window, len(df)):
        dev = df[dev_col].iloc[i]
        if pd.isna(dev):
            continue
        if not (cost_cfg["entry_min_deviation_pct"] <= dev <= cost_cfg["entry_max_deviation_pct"]):
            continue

        cost_now = df[cost_col].iloc[i]
        cost_before = df[cost_col].iloc[i - trend_window]
        if cost_before <= 0 or cost_now <= cost_before:
            continue  # cost line must be rising — institutions still accumulating, not flat/distributing

        signals.add(df["date"].iloc[i])

    return signals
