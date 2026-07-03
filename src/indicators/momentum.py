"""Continuous momentum features, as an alternative to boolean thresholds.

The external quant review's critique: existing features describe state
("bought today: yes/no") rather than trajectory ("buying is accelerating").
A boolean AND-of-conditions rule engine can't use "3 features that are each
weakly positive" — entry_exit_signal.py either counts a condition as fully
met or not at all. These functions expose the underlying continuous values
(rolling slope of institutional/broker net buying, volume ratio) so a
scoring model can combine partial evidence instead of discarding it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _slope(values: list[float] | pd.Series) -> float:
    """Simple linear regression slope over the given series' index order.
    Returns 0.0 if fewer than 2 points."""
    y = np.asarray(values, dtype=float)
    n = len(y)
    if n < 2:
        return 0.0
    x = np.arange(n, dtype=float)
    x_mean, y_mean = x.mean(), y.mean()
    denom = ((x - x_mean) ** 2).sum()
    if denom == 0:
        return 0.0
    return float(((x - x_mean) * (y - y_mean)).sum() / denom)


def institutional_momentum(inst_df: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Adds foreign_net_slope / trust_net_slope: the rolling linear-regression
    slope of daily net buy over the trailing window — positive and growing
    means institutional buying is *accelerating*, not just present."""
    df = inst_df.sort_values("date").reset_index(drop=True).copy()
    df["foreign_net_slope"] = df["foreign_net"].rolling(window_days, min_periods=2).apply(_slope, raw=False)
    df["trust_net_slope"] = df["trust_net"].rolling(window_days, min_periods=2).apply(_slope, raw=False)
    return df


def broker_momentum(daily_net_series: pd.Series, window_days: int) -> float:
    """Slope of a single broker's daily net-buy series over its most recent
    `window_days` — the numeric version of broker_streak.py's "trend"
    (increasing/decreasing), which only reports the sign."""
    tail = daily_net_series.tail(window_days)
    return _slope(tail)


def volume_ratio(volume: int, avg_volume: float) -> float:
    """Continuous version of entry_exit_signal's boolean "成交量高於均量" —
    exposes how far above/below average, not just a yes/no at 1.0x."""
    return volume / avg_volume if avg_volume > 0 else 0.0


def zscore(value: float, series: pd.Series) -> float:
    """How many standard deviations `value` is from `series`'s mean — puts
    features with different natural scales (slopes in shares/day, ratios in
    multiples of 1.0) onto a comparable footing before combining them."""
    std = series.std()
    if std == 0 or pd.isna(std):
        return 0.0
    return float((value - series.mean()) / std)
