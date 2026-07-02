"""Rule engine: bull/bear light and sell-off alert.

Both are plain rule combinations over already-computed indicators — no
hidden magic, so every trigger can be traced back to a concrete number.
"""
from __future__ import annotations


def bull_bear_light(chip_health_score: float, config: dict) -> dict:
    bullish_min = config["signal_light"]["bullish_min_score"]
    bearish_max = config["signal_light"]["bearish_max_score"]

    if chip_health_score >= bullish_min:
        return {"light": "🟢🟢🟢🟢🟢", "label": "強多"}
    if chip_health_score <= bearish_max:
        return {"light": "🔴🔴🔴🔴", "label": "偏空"}
    return {"light": "🟡🟡🟡", "label": "整理"}


def sell_off_alert(
    broker_sell_streak_days: int,
    pnl_pct: float | None,
    vp_pattern: str,
    margin_risk_level: str,
    false_breakout_risk: bool,
    config: dict,
) -> dict:
    cfg = config["sell_alert"]
    conditions = {
        "分點連續賣超": broker_sell_streak_days >= cfg["consecutive_sell_days"],
        "股價跌破主力成本": (pnl_pct is not None) and (pnl_pct < cfg["price_below_cost_pct"]),
        "量增價跌": vp_pattern == "價跌量增",
        "融資維持率轉危險": margin_risk_level == "danger",
        "假突破風險": false_breakout_risk,
    }
    triggered = [name for name, hit in conditions.items() if hit]
    alert = len(triggered) >= cfg["min_conditions_triggered"]
    return {
        "alert": alert,
        "triggered_conditions": triggered,
        "message": "⚠️ 主力疑似出貨" if alert else "",
    }
