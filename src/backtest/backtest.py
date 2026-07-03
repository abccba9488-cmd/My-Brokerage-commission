"""Forward-return backtest for a boolean signal series.

This is the module that actually answers "does this signal work" instead of
letting a rule-based score speak for itself. Any indicator in src/indicators
can be turned into a signal_dates list and run through here before it's
trusted.
"""
from __future__ import annotations

import pandas as pd

# A calendar-day gap this large between two consecutive trading rows means the
# stock was halted (資產重整/減資/停牌), not just a weekend or normal holiday
# (Taiwan's longest routine holiday, Lunar New Year, runs ~9-10 days). Trades
# spanning a halt aren't tradeable and their "return" is a reference-price
# reset, not a real gain/loss — e.g. 1435 (中福) jumped 12.85 -> 5.39 -> 14.10
# across two halts in 2026, which inflated pooled max-drawdown to 60-70%
# before this filter existed.
_SUSPENSION_GAP_DAYS = 15


def _suspension_gap_indices(df: pd.DataFrame) -> set[int]:
    dates = pd.to_datetime(df["date"])
    gap_days = dates.diff().dt.days
    return set(df.index[gap_days > _SUSPENSION_GAP_DAYS])


def _trades_for_holding(price_df: pd.DataFrame, signal_dates: set[str], holding: int) -> list[dict]:
    df = price_df.sort_values("date").reset_index(drop=True)
    date_to_idx = {d: i for i, d in enumerate(df["date"])}
    gap_indices = _suspension_gap_indices(df)

    trades = []
    for sig_date in sorted(signal_dates):
        if sig_date not in date_to_idx:
            continue
        entry_idx = date_to_idx[sig_date]
        exit_idx = entry_idx + holding
        if exit_idx >= len(df):
            continue  # not enough forward data yet
        if any(entry_idx < g <= exit_idx for g in gap_indices):
            continue  # holding period spans a trading halt — not a real tradeable return

        entry_close = df["close"].iloc[entry_idx]
        exit_close = df["close"].iloc[exit_idx]
        path = df["close"].iloc[entry_idx: exit_idx + 1]

        ret_pct = (exit_close / entry_close - 1) * 100
        max_drawdown_pct = ((path.cummax() - path) / path.cummax() * 100).max()

        trades.append({"signal_date": sig_date, "return_pct": ret_pct, "max_drawdown_pct": max_drawdown_pct})
    return trades


def _summarize(trades: list[dict]) -> dict:
    if not trades:
        return {"sample_count": 0}
    trades_df = pd.DataFrame(trades)
    return {
        "sample_count": len(trades_df),
        "win_rate_pct": round((trades_df["return_pct"] > 0).mean() * 100, 1),
        "avg_return_pct": round(trades_df["return_pct"].mean(), 2),
        "median_return_pct": round(trades_df["return_pct"].median(), 2),
        "max_drawdown_pct": round(trades_df["max_drawdown_pct"].max(), 2),
    }


def run(price_df: pd.DataFrame, signal_dates: set[str], holding_days_list: list[int]) -> dict:
    """price_df: columns date, close, sorted ascending. signal_dates: set of
    'date' strings on which the signal fired. Returns per-holding-period stats.
    """
    return {h: _summarize(_trades_for_holding(price_df, signal_dates, h)) for h in holding_days_list}


def run_multi(stock_signal_pairs: list[tuple[pd.DataFrame, set[str]]], holding_days_list: list[int]) -> dict:
    """Same as run(), but pools trades across multiple stocks for a larger
    sample size. stock_signal_pairs: list of (price_df, signal_dates)."""
    results = {}
    for holding in holding_days_list:
        pooled_trades: list[dict] = []
        for price_df, signals in stock_signal_pairs:
            pooled_trades.extend(_trades_for_holding(price_df, signals, holding))
        results[holding] = _summarize(pooled_trades)
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
