"""Classic technical indicators for swing trading (波段操作): KD (stochastic),
MACD, and Bollinger Bands. Pure price/volume math, no chip-flow data —
these are the most widely-used, most heavily arbitraged indicators in
retail trading, so this module exists to test them with the same rigor as
everything else here, not to assume they work because they're popular.
"""
from __future__ import annotations

import pandas as pd


def kd(price_df: pd.DataFrame, period: int = 9) -> pd.DataFrame:
    """Taiwan-style KD: RSV over `period` days, then K/D smoothed 2/3-1/3
    (equivalent to an alpha=1/3 EMA), seeded at 50."""
    df = price_df.sort_values("date").reset_index(drop=True).copy()
    low_n = df["low"].rolling(period, min_periods=1).min()
    high_n = df["high"].rolling(period, min_periods=1).max()
    rng = (high_n - low_n).replace(0, pd.NA)
    rsv = ((df["close"] - low_n) / rng * 100).fillna(50)

    k_vals, d_vals = [], []
    k_prev, d_prev = 50.0, 50.0
    for r in rsv:
        k_prev = (2 / 3) * k_prev + (1 / 3) * r
        d_prev = (2 / 3) * d_prev + (1 / 3) * k_prev
        k_vals.append(k_prev)
        d_vals.append(d_prev)

    df["k"] = k_vals
    df["d"] = d_vals
    return df


def macd(price_df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    df = price_df.sort_values("date").reset_index(drop=True).copy()
    ema_fast = df["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = df["close"].ewm(span=slow, adjust=False).mean()
    df["macd_line"] = ema_fast - ema_slow
    df["signal_line"] = df["macd_line"].ewm(span=signal, adjust=False).mean()
    df["histogram"] = df["macd_line"] - df["signal_line"]
    return df


def bollinger(price_df: pd.DataFrame, period: int = 20, std_dev: float = 2.0) -> pd.DataFrame:
    df = price_df.sort_values("date").reset_index(drop=True).copy()
    ma = df["close"].rolling(period, min_periods=1).mean()
    std = df["close"].rolling(period, min_periods=1).std().fillna(0)
    df["bb_mid"] = ma
    df["bb_upper"] = ma + std_dev * std
    df["bb_lower"] = ma - std_dev * std
    return df
