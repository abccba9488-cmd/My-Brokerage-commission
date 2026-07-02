"""Entry / stop-loss / take-profit rule evaluation.

Implements the trading rules from the original multi-AI discussion's "建議行動"
section: enter on >= N of 5 conditions, stop-loss on cost breakdown, take-profit
on extended gains + distribution signs. This is a rule checklist, not a
prediction — every trigger is traceable to the inputs below. Two of the five
entry conditions and one exit condition need broker branch (分點) data; when
`broker_available` is False they're simply counted as not-met rather than
guessed, so a BUY never fires on incomplete data.
"""
from __future__ import annotations


def evaluate_entry(
    broker_available: bool,
    broker_streak_days: int,
    broker_direction: str,  # "buy" / "sell" / "flat" / "none"
    csi: float,
    foreign_net: int,
    trust_net: int,
    pnl_pct: float | None,   # price vs. estimated broker cost
    volume: int,
    avg_volume: float,
    config: dict,
) -> dict:
    cfg = config["entry_exit"]

    conditions = {
        "主力連續3-5日買超": broker_available and broker_direction == "buy" and broker_streak_days >= config["broker"]["streak_min_days"],
        "分點集中度偏高": broker_available and csi >= 20,
        "外資或投信同步買超": foreign_net > 0 or trust_net > 0,
        "股價站穩主力成本區": broker_available and pnl_pct is not None and pnl_pct >= 0,
        "成交量高於均量": avg_volume > 0 and volume >= avg_volume * cfg["volume_surge_ratio"],
    }
    met = [name for name, hit in conditions.items() if hit]
    unavailable = [] if broker_available else ["主力連續3-5日買超", "分點集中度偏高", "股價站穩主力成本區"]

    return {
        "action": "BUY" if len(met) >= cfg["entry_min_conditions"] else "HOLD",
        "conditions_met": met,
        "conditions_total": len(conditions),
        "conditions_unavailable": unavailable,
    }


def evaluate_exit(
    broker_available: bool,
    pnl_pct: float | None,
    close: float,
    ma_long: float,
    broker_sell_streak_days: int,
    sell_alert_triggered: bool,
    config: dict,
) -> dict:
    cfg = config["entry_exit"]

    stop_loss_reasons = []
    if pnl_pct is not None and pnl_pct <= cfg["stop_loss_pct"]:
        stop_loss_reasons.append(f"跌破主力成本 {cfg['stop_loss_pct']}%")
    if close < ma_long and broker_sell_streak_days >= config["sell_alert"]["consecutive_sell_days"]:
        stop_loss_reasons.append("跌破均線且主力連續賣超")
    if sell_alert_triggered:
        stop_loss_reasons.append("出貨警報已觸發")

    take_profit_reasons = []
    if pnl_pct is not None and pnl_pct >= cfg["take_profit_min_pct"]:
        if broker_available and broker_sell_streak_days > 0:
            take_profit_reasons.append(f"獲利超過 {cfg['take_profit_min_pct']}% 且分點開始轉賣")
        elif not broker_available:
            take_profit_reasons.append(f"獲利超過 {cfg['take_profit_min_pct']}%（無分點資料可確認是否轉賣，僅供參考）")

    if stop_loss_reasons:
        action = "STOP_LOSS"
    elif take_profit_reasons:
        action = "TAKE_PROFIT"
    else:
        action = "HOLD"

    return {
        "action": action,
        "stop_loss_reasons": stop_loss_reasons,
        "take_profit_reasons": take_profit_reasons,
    }
