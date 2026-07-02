"""Composite '籌碼健康度' (chip health score), 0~100.

A weighted rollup across broker accumulation, foreign/trust institutional
flow, margin risk, and securities lending. Like accumulation_score, this is
a documented heuristic, not a validated model — see src/backtest before
trusting it for sizing decisions.
"""
from __future__ import annotations

MAX_POINTS = {
    "broker": 30,
    "foreign": 20,
    "trust": 20,
    "margin": 15,
    "lending": 15,
}


def compute(
    accumulation_score: float | None,
    foreign_net: int,
    trust_net: int,
    margin_risk_level: str,
    lending_balance_trend: str,
) -> dict:
    broker_pts = MAX_POINTS["broker"] * ((accumulation_score or 0) / 100)

    foreign_pts = MAX_POINTS["foreign"] if foreign_net > 0 else (
        MAX_POINTS["foreign"] * 0.3 if foreign_net == 0 else 0
    )
    trust_pts = MAX_POINTS["trust"] if trust_net > 0 else (
        MAX_POINTS["trust"] * 0.3 if trust_net == 0 else 0
    )

    margin_pts = {"safe": MAX_POINTS["margin"], "warning": MAX_POINTS["margin"] * 0.5, "danger": 0}.get(
        margin_risk_level, MAX_POINTS["margin"] * 0.5
    )

    # Rising lending (借券) balance signals building short pressure -> lower health.
    lending_pts = {"decreasing": MAX_POINTS["lending"], "flat": MAX_POINTS["lending"] * 0.6, "increasing": 0}.get(
        lending_balance_trend, MAX_POINTS["lending"] * 0.6
    )

    total = broker_pts + foreign_pts + trust_pts + margin_pts + lending_pts
    label = "偏多" if total >= 70 else ("中性" if total >= 40 else "偏空")

    return {
        "score": round(total, 1),
        "label": label,
        "breakdown": {
            "broker": round(broker_pts, 1),
            "foreign": round(foreign_pts, 1),
            "trust": round(trust_pts, 1),
            "margin": round(margin_pts, 1),
            "lending": round(lending_pts, 1),
        },
    }
