"""Entry signal definitions for the 3 classic technical indicators
(src/indicators/technical.py), each using the most standard textbook rule
rather than a tuned variant — the point is testing the popular version,
not fitting a version to this dataset.
"""
from __future__ import annotations

import pandas as pd

from src.indicators import technical


def kd_signal_dates(price_df: pd.DataFrame, config: dict) -> set[str]:
    """低檔黃金交叉: K crosses above D while both are below the oversold threshold."""
    cfg = config["technical"]["kd"]
    df = technical.kd(price_df, cfg["period"])
    k, d = df["k"], df["d"]
    cross_up = (k.shift(1) <= d.shift(1)) & (k > d)
    oversold = (k < cfg["oversold_threshold"]) & (d < cfg["oversold_threshold"])
    mask = cross_up & oversold
    return set(df.loc[mask, "date"])


def macd_signal_dates(price_df: pd.DataFrame, config: dict) -> set[str]:
    """MACD黃金交叉: MACD line crosses above its signal line."""
    cfg = config["technical"]["macd"]
    df = technical.macd(price_df, cfg["fast"], cfg["slow"], cfg["signal"])
    m, s = df["macd_line"], df["signal_line"]
    cross_up = (m.shift(1) <= s.shift(1)) & (m > s)
    return set(df.loc[cross_up, "date"])


def bollinger_signal_dates(price_df: pd.DataFrame, config: dict) -> set[str]:
    """觸及下軌反彈: close broke below the lower band yesterday, closes back above it today."""
    cfg = config["technical"]["bollinger"]
    df = technical.bollinger(price_df, cfg["period"], cfg["std_dev"])
    below_yesterday = df["close"].shift(1) < df["bb_lower"].shift(1)
    back_above_today = df["close"] >= df["bb_lower"]
    mask = below_yesterday & back_above_today
    return set(df.loc[mask, "date"])
