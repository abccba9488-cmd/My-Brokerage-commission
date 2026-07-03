"""Margin (融資) maintenance-ratio risk estimation.

We don't have per-account margin purchase price (that's broker-side private
data), so we approximate the weighted-average margin cost using each day's
closing price weighted by that day's margin_buy volume over a lookback
window. Taiwan's standard margin financing ratio is ~60% (investor funds the
remaining 40%), so:

    maintenance_ratio(%) = close / (margin_cost_estimate * 0.6) * 100

This is a simplification (real broker maintenance ratios use actual cost
basis and vary by security), used only to flag directional risk, not as an
exact number.
"""
from __future__ import annotations

import pandas as pd

FINANCING_RATIO = 0.6


def compute(price_df: pd.DataFrame, margin_df: pd.DataFrame, lookback_days: int, config: dict) -> pd.DataFrame:
    """price_df: date, close. margin_df: date, margin_buy, margin_balance.
    Returns price_df merged with margin_cost_estimate, maintenance_ratio_pct, risk_level.
    """
    df = price_df.merge(margin_df[["date", "margin_buy", "margin_balance"]], on="date", how="left")
    df = df.sort_values("date").reset_index(drop=True)
    df["margin_buy"] = pd.to_numeric(df["margin_buy"], errors="coerce").fillna(0)

    weighted_cost = []
    for i in range(len(df)):
        window = df.iloc[max(0, i - lookback_days + 1): i + 1]
        weights = window["margin_buy"]
        if weights.sum() > 0:
            cost = (window["close"] * weights).sum() / weights.sum()
        else:
            cost = df["close"].iloc[i]  # no margin buying observed; fall back to spot price
        weighted_cost.append(cost)
    df["margin_cost_estimate"] = weighted_cost

    df["maintenance_ratio_pct"] = (df["close"] / (df["margin_cost_estimate"] * FINANCING_RATIO)) * 100

    warn = config["margin"]["maintenance_ratio_warning_pct"]
    danger = config["margin"]["maintenance_ratio_danger_pct"]

    def _risk(ratio: float) -> str:
        if ratio <= danger:
            return "danger"
        if ratio <= warn:
            return "warning"
        return "safe"

    df["risk_level"] = df["maintenance_ratio_pct"].apply(_risk)
    return df


def latest(price_df: pd.DataFrame, margin_df: pd.DataFrame, lookback_days: int, config: dict) -> dict:
    df = compute(price_df, margin_df, lookback_days, config)
    if df.empty:
        return {}
    row = df.iloc[-1]
    return {
        "margin_cost_estimate": round(row["margin_cost_estimate"], 2),
        "maintenance_ratio_pct": round(row["maintenance_ratio_pct"], 1),
        "risk_level": row["risk_level"],
    }
