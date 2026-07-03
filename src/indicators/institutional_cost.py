"""法人成本線 (institutional cost line): approximate cost basis for foreign
investors (外資), investment trusts (投信), and dealers (自營商), computed
the same way as margin_risk.py's financing cost line — closing price
weighted by that day's net-buy volume over a lookback window, since we
don't have per-account transaction prices (that's broker-side private
data, same limitation as margin accounts).

This is a DISPLAY indicator, not yet a validated trading signal. The
underlying trading heuristics (投信季底護盤, 外資成本線=支撐, 乖離率分級
timing) are market folklore, not something this codebase has backtested —
see src/backtest before trusting them the way run_daily.py's other BUY/
STOP_LOSS rules already are (those went through FDR-corrected backtesting;
this hasn't yet).
"""
from __future__ import annotations

import pandas as pd

_QUARTER_END_MONTHS = {3, 6, 9, 12}
_QUARTER_END_WINDOW_DAYS = 10  # "近季底" if within this many calendar days of a quarter-end


def _weighted_cost_series(close: pd.Series, net_buy: pd.Series, lookback_days: int) -> pd.Series:
    """Closing price weighted by net-buy volume (only positive/buying days
    count toward the weight) over a trailing window — same technique as
    margin_risk.py's financing cost line."""
    buy_weight = net_buy.clip(lower=0)
    out = []
    for i in range(len(close)):
        start = max(0, i - lookback_days + 1)
        w = buy_weight.iloc[start: i + 1]
        c = close.iloc[start: i + 1]
        out.append((c * w).sum() / w.sum() if w.sum() > 0 else close.iloc[i])
    return pd.Series(out, index=close.index)


def deviation_zone(deviation_pct: float | None) -> str:
    """Classifies price-vs-cost deviation into the bands the user described:
    just-finished-building -> safe margin -> profit-taking-sensitive ->
    stretched, and the downside (underwater) cases."""
    if deviation_pct is None:
        return "無資料"
    if deviation_pct < -10:
        return "法人套牢（觀察是否停損）"
    if deviation_pct < 0:
        return "小幅低於成本"
    if deviation_pct <= 5:
        return "貼近成本（剛建倉，安全邊際最高）"
    if deviation_pct <= 15:
        return "安全邊際內"
    if deviation_pct <= 20:
        return "獲利豐厚（季底結帳敏感區，慎防追高）"
    return "大幅超漲（乖離過大）"


def near_quarter_end(date_str: str) -> bool:
    """Within ~10 calendar days of a quarter-end (3/31, 6/30, 9/30, 12/31) —
    the window 投信 defensive buying (護盤) is most associated with, per
    fund performance-ranking pressure at those reporting dates."""
    d = pd.Timestamp(date_str)
    if d.month in _QUARTER_END_MONTHS:
        quarter_end = d + pd.offsets.QuarterEnd(0)
        return abs((quarter_end - d).days) <= _QUARTER_END_WINDOW_DAYS
    return False


def compute(price_df: pd.DataFrame, inst_df: pd.DataFrame, lookback_days: int) -> pd.DataFrame:
    """price_df: date, close. inst_df: date, foreign_net, trust_net, dealer_net
    (raw shares, as fetched — sign/relative magnitude is all that matters here).
    Returns price_df merged with a cost estimate + deviation % per investor type."""
    df = price_df.sort_values("date").reset_index(drop=True)
    inst = inst_df[["date", "foreign_net", "trust_net", "dealer_net"]] if not inst_df.empty else pd.DataFrame(
        columns=["date", "foreign_net", "trust_net", "dealer_net"]
    )
    df = df.merge(inst, on="date", how="left")
    for col in ("foreign_net", "trust_net", "dealer_net"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    for label, col in (("foreign", "foreign_net"), ("trust", "trust_net"), ("dealer", "dealer_net")):
        df[f"{label}_cost"] = _weighted_cost_series(df["close"], df[col], lookback_days)
        df[f"{label}_deviation_pct"] = (df["close"] - df[f"{label}_cost"]) / df[f"{label}_cost"] * 100

    return df


def latest(price_df: pd.DataFrame, inst_df: pd.DataFrame, lookback_days: int) -> dict:
    df = compute(price_df, inst_df, lookback_days)
    if df.empty:
        return {}
    row = df.iloc[-1]
    result = {"near_quarter_end": near_quarter_end(row["date"])}
    for label in ("foreign", "trust", "dealer"):
        cost = row[f"{label}_cost"]
        dev = row[f"{label}_deviation_pct"]
        result[label] = {
            "cost": round(float(cost), 2),
            "deviation_pct": round(float(dev), 2),
            "zone": deviation_zone(float(dev)),
        }
    return result
