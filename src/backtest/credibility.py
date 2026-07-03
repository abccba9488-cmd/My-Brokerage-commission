"""Turn backtest edge-over-baseline results into a per-stock credibility grade.

This exists so run_daily.py doesn't present every stock's BUY/HOLD signal as
equally trustworthy. The 2026-07 backtest of the broker buy-streak signal
found a real, consistent edge on 2317 and essentially none on 2884/2330 in
the same 1-year window — grading makes that difference visible in the daily
report instead of burying it in a backtest file nobody re-reads.

2026-07-03 update: grading 38 stocks independently and calling the
best-looking ones "A" is the multiple-comparisons trap. Applying Mann-Whitney
+ Benjamini-Hochberg FDR correction (see significance.py) across all 38
stocks found **zero** survive at FDR=10% — including the 4 that originally
scored A here. A grade now requires FDR significance as well as the edge
heuristics below; without it, the ceiling is B ("directionally promising,
not statistically proven") no matter how good the raw edge numbers look.

Never hand-write a grade; always derive it from a fresh run_backtest.py run.
"""
from __future__ import annotations

ROUND_TRIP_COST_PCT = 0.6  # rough estimate: ~0.3% transaction tax + ~0.15% each-way brokerage
MIN_SAMPLE_COUNT = 20
STRONG_EDGE_PP = 5.0


def grade(
    signal_10d: dict,
    baseline_10d: dict,
    signal_20d: dict,
    baseline_20d: dict,
    fdr_significant: bool | None = None,
) -> dict:
    """Grades using the 10-day and 20-day holding periods (less noisy than
    3/5-day). `fdr_significant`: result of a Mann-Whitney test against this
    stock's baseline, corrected for multiple comparisons across the whole
    watchlist (see run_significance_analysis.py). None means "not tested" —
    the grade is then capped at B and the reason says so explicitly, rather
    than silently treating an untested stock as equivalent to a disproven one.

    Returns {"grade": "A"/"B"/"C"/"D"/"N/A", "reason": str}."""
    if signal_10d.get("sample_count", 0) < MIN_SAMPLE_COUNT or signal_20d.get("sample_count", 0) < MIN_SAMPLE_COUNT:
        return {"grade": "N/A", "reason": f"樣本數不足（<{MIN_SAMPLE_COUNT}次），無法評級"}

    win_edge_10 = signal_10d["win_rate_pct"] - baseline_10d["win_rate_pct"]
    win_edge_20 = signal_20d["win_rate_pct"] - baseline_20d["win_rate_pct"]
    ret_edge_10 = signal_10d["avg_return_pct"] - baseline_10d["avg_return_pct"]
    ret_edge_20 = signal_20d["avg_return_pct"] - baseline_20d["avg_return_pct"]

    both_positive = win_edge_10 > 0 and win_edge_20 > 0 and ret_edge_10 > 0 and ret_edge_20 > 0
    either_meaningfully_negative = win_edge_10 < -1 or win_edge_20 < -1 or ret_edge_10 < -0.3 or ret_edge_20 < -0.3
    clears_costs = (
        signal_10d["avg_return_pct"] > ROUND_TRIP_COST_PCT and signal_20d["avg_return_pct"] > ROUND_TRIP_COST_PCT
    )
    strong_edge = win_edge_10 >= STRONG_EDGE_PP and win_edge_20 >= STRONG_EDGE_PP
    edge_qualifies_for_a = both_positive and strong_edge and clears_costs

    if either_meaningfully_negative:
        return {
            "grade": "D",
            "reason": f"訊號比基準線差（10日勝率差{win_edge_10:+.1f}pp／20日{win_edge_20:+.1f}pp），不建議依此訊號進出場",
        }

    if edge_qualifies_for_a:
        if fdr_significant is True:
            return {
                "grade": "A",
                "reason": (
                    f"10日與20日勝率、報酬都優於基準線（10日{win_edge_10:+.1f}pp／20日{win_edge_20:+.1f}pp），"
                    f"報酬扣掉約{ROUND_TRIP_COST_PCT}%交易成本估算後仍有空間，且經 FDR 校正後統計顯著"
                ),
            }
        caveat = "尚未做過 FDR 顯著性檢定" if fdr_significant is None else "但經 FDR 校正後不具統計顯著性（可能是運氣）"
        return {
            "grade": "B",
            "reason": (
                f"優勢數字看起來像 A 級（10日{win_edge_10:+.1f}pp／20日{win_edge_20:+.1f}pp），{caveat}，"
                f"上限降為 B——單看邊際數字會高估可信度"
            ),
        }

    if both_positive:
        return {
            "grade": "B",
            "reason": (
                f"方向上優於基準線但優勢較小或報酬接近交易成本門檻"
                f"（10日{win_edge_10:+.1f}pp／20日{win_edge_20:+.1f}pp）"
            ),
        }
    return {"grade": "C", "reason": "優勢不一致或接近0，目前資料看不出訊號比隨機進場更好"}
