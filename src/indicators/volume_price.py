"""Volume-price structure classification and divergence (背離) detection."""
from __future__ import annotations

import pandas as pd


def compute(price_df: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
    df = price_df.sort_values("date").reset_index(drop=True).copy()
    df["avg_volume"] = df["volume"].rolling(lookback_days, min_periods=1).mean()
    df["rolling_high"] = df["close"].rolling(lookback_days, min_periods=1).max()
    df["ma_long"] = df["close"].rolling(lookback_days, min_periods=1).mean()

    prev_close = df["close"].shift(1)
    prev_volume = df["volume"].shift(1)

    def _pattern(row_close, row_prev_close, row_vol, row_prev_vol) -> str:
        if pd.isna(row_prev_close) or pd.isna(row_prev_vol):
            return "unknown"
        price_up = row_close >= row_prev_close
        vol_up = row_vol >= row_prev_vol
        if price_up and vol_up:
            return "價漲量增"
        if price_up and not vol_up:
            return "價漲量縮"
        if not price_up and vol_up:
            return "價跌量增"
        return "價跌量縮"

    df["vp_pattern"] = [
        _pattern(c, pc, v, pv)
        for c, pc, v, pv in zip(df["close"], prev_close, df["volume"], prev_volume)
    ]

    # False-breakout flag: today's close is a new N-day high but volume is below the N-day average.
    df["is_new_high"] = df["close"] >= df["rolling_high"]
    df["false_breakout_risk"] = df["is_new_high"] & (df["volume"] < df["avg_volume"])

    return df


def latest(price_df: pd.DataFrame, lookback_days: int) -> dict:
    df = compute(price_df, lookback_days)
    if df.empty:
        return {}
    row = df.iloc[-1]
    return {
        "vp_pattern": row["vp_pattern"],
        "false_breakout_risk": bool(row["false_breakout_risk"]),
        "avg_volume": round(row["avg_volume"], 0),
        "ma_long": round(row["ma_long"], 2),
        "volume": int(row["volume"]),
    }
