"""Composite '主力吸籌指數' (accumulation score), 0~100.

Weighted combination inspired by the multi-AI discussion (streak, volume
share, concentration, cost trend, price position, institutional sync). All
weights and normalization caps are simple, documented heuristics — not
calibrated against a backtest. Treat as an observational score, not a
validated predictor, until backtested (see src/backtest).
"""
from __future__ import annotations

WEIGHTS = {
    "streak": 0.20,
    "volume_share": 0.20,
    "concentration": 0.20,
    "cost_trend": 0.15,
    "price_position": 0.15,
    "institutional_sync": 0.10,
}


def _clip(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def compute(
    streak_days: int,
    net_buy_volume: int,
    period_volume: int,
    csi: float,
    cost_trend: str,
    pnl_pct: float | None,
    foreign_net: int,
    trust_net: int,
) -> dict:
    streak_score = _clip(streak_days / 10 * 100)

    volume_share_pct = (net_buy_volume / period_volume * 100) if period_volume else 0
    volume_share_score = _clip(volume_share_pct / 30 * 100)

    concentration_score = _clip(csi)

    cost_trend_score = {"increasing": 100, "decreasing": 40, "unknown": 50}.get(cost_trend, 50)

    # Sweet spot for accumulation: price sitting modestly above cost (0~15%).
    # Too far above cost (>30%) reads as distribution risk, not accumulation.
    if pnl_pct is None:
        price_position_score = 50
    elif pnl_pct < 0:
        price_position_score = _clip(70 + pnl_pct)  # still underwater = mild positive (still loading)
    elif pnl_pct <= 15:
        price_position_score = 100
    elif pnl_pct <= 30:
        price_position_score = 60
    else:
        price_position_score = 20

    institutional_sync_score = 100 if (foreign_net > 0 or trust_net > 0) else 30

    total = (
        streak_score * WEIGHTS["streak"]
        + volume_share_score * WEIGHTS["volume_share"]
        + concentration_score * WEIGHTS["concentration"]
        + cost_trend_score * WEIGHTS["cost_trend"]
        + price_position_score * WEIGHTS["price_position"]
        + institutional_sync_score * WEIGHTS["institutional_sync"]
    )

    return {
        "score": round(total, 1),
        "components": {
            "streak_score": round(streak_score, 1),
            "volume_share_score": round(volume_share_score, 1),
            "concentration_score": round(concentration_score, 1),
            "cost_trend_score": cost_trend_score,
            "price_position_score": round(price_position_score, 1),
            "institutional_sync_score": institutional_sync_score,
        },
    }
