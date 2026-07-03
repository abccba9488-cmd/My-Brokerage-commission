"""Rolling train/test fold generation for walk-forward validation.

Fitting a signal's parameters (thresholds, normalization stats, broker
personality labels) on the same period you then backtest it on is
look-ahead bias — you wouldn't have had that information yet on day 1 of
the period. Walk-forward splits history into rolling (train, test) windows
so every test-period result is genuinely out-of-sample.

With only ~1 year of history this produces a single fold (barely more than
a train/test split); it becomes a real multi-fold walk-forward once the
extended multi-year backfill (see run_backtest.py --days) is available —
callers should report the fold count so results aren't over-interpreted
from a single fold.
"""
from __future__ import annotations

import pandas as pd


def generate_folds(
    dates: list[str],
    train_months: int = 9,
    test_months: int = 3,
    step_months: int = 3,
) -> list[dict]:
    """Rolling folds over a sorted list of trading-date strings ('YYYY-MM-DD').
    Returns [{"train_start", "train_end", "test_start", "test_end"}, ...],
    where train_end == test_start (test starts the day after train ends, in
    calendar terms — callers filter their own date lists against these
    boundaries, so exact trading-day alignment isn't required here)."""
    if not dates:
        return []
    dates_dt = pd.to_datetime(sorted(dates))
    overall_start, overall_end = dates_dt.min(), dates_dt.max()

    folds = []
    train_start = overall_start
    while True:
        train_end = train_start + pd.DateOffset(months=train_months)
        test_end = train_end + pd.DateOffset(months=test_months)
        if test_end > overall_end:
            break
        folds.append({
            "train_start": train_start.strftime("%Y-%m-%d"),
            "train_end": train_end.strftime("%Y-%m-%d"),
            "test_start": train_end.strftime("%Y-%m-%d"),
            "test_end": test_end.strftime("%Y-%m-%d"),
        })
        train_start = train_start + pd.DateOffset(months=step_months)
    return folds


def split_by_date(df: pd.DataFrame, start: str, end: str, date_col: str = "date") -> pd.DataFrame:
    return df[(df[date_col] >= start) & (df[date_col] < end)].reset_index(drop=True)
