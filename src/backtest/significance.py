"""Statistical significance testing for backtest results.

Grading 38 stocks independently and calling the best-looking ones "A grade"
is exactly the multiple-comparisons trap: run enough independent tests and
some will look significant by chance alone (expect ~1-2 false positives out
of 38 even if nothing is real, at a naive 5% significance level per test).

This module adds two corrections on top of credibility.py's edge-over-
baseline heuristic:

1. Mann-Whitney U test per stock: is the signal's return distribution
   actually different from the baseline's, or could the observed edge
   plausibly be noise? Nonparametric because trade returns are skewed/
   fat-tailed, not normal.
2. Benjamini-Hochberg FDR correction across all stocks' p-values: controls
   the expected proportion of false discoveries among stocks flagged as
   "significant", rather than treating each test's 5% error rate as if it
   applied in isolation.
"""
from __future__ import annotations

import numpy as np
from scipy import stats


def mann_whitney_test(signal_returns: list[float], baseline_returns: list[float]) -> dict:
    """One-sided test: are signal returns stochastically greater than baseline?
    Returns {"p_value": float, "u_statistic": float} or p_value=1.0 if either
    sample is too small to test meaningfully."""
    if len(signal_returns) < 5 or len(baseline_returns) < 5:
        return {"p_value": 1.0, "u_statistic": None}
    u_stat, p_value = stats.mannwhitneyu(signal_returns, baseline_returns, alternative="greater")
    return {"p_value": float(p_value), "u_statistic": float(u_stat)}


def benjamini_hochberg(p_values: dict[str, float], fdr: float = 0.10) -> dict[str, dict]:
    """Standard BH step-up procedure. p_values: {key: p_value}. fdr: target
    false discovery rate (0.10 is more lenient than the usual 0.05 — with
    only ~38 tests and small per-stock samples, 0.05 would likely flag zero
    stocks as significant; 0.10 is a defensible middle ground for an
    exploratory analysis, not a publication-grade claim).

    Returns {key: {"p_value", "rank", "threshold", "significant"}}.
    """
    items = sorted(p_values.items(), key=lambda kv: kv[1])
    n = len(items)
    if n == 0:
        return {}

    # Find the largest rank k where p(k) <= (k/n) * fdr; everything at or
    # below that rank is a discovery.
    largest_significant_rank = 0
    for rank, (_, p) in enumerate(items, start=1):
        threshold = (rank / n) * fdr
        if p <= threshold:
            largest_significant_rank = rank

    out = {}
    for rank, (key, p) in enumerate(items, start=1):
        threshold = (rank / n) * fdr
        out[key] = {
            "p_value": p,
            "rank": rank,
            "bh_threshold": round(threshold, 4),
            "significant": rank <= largest_significant_rank,
        }
    return out


def deflated_sharpe_ratio(returns: list[float], num_trials: int, benchmark_sharpe: float = 0.0) -> dict:
    """Probability that the observed Sharpe ratio is genuinely > 0, after
    accounting for having tried `num_trials` variations (each additional
    trial raises the Sharpe ratio you'd expect to see from pure luck alone).
    Based on Bailey & Lopez de Prado's Deflated Sharpe Ratio.

    num_trials should count every distinct signal/threshold configuration
    actually run against this data, not just the ones reported — e.g. our
    default streak, the streak_min_days=5/volume_share_min_pct=20
    experiment, and the composite signal are 3 known trials; treat this as
    a lower bound, not an exact count.
    """
    n = len(returns)
    if n < 10:
        return {"deflated_sharpe": None, "reason": f"樣本數不足（{n}<10），無法估計"}

    returns_arr = np.array(returns)
    sharpe = returns_arr.mean() / returns_arr.std(ddof=1) if returns_arr.std(ddof=1) > 0 else 0.0
    skew = float(stats.skew(returns_arr))
    kurt = float(stats.kurtosis(returns_arr, fisher=False))  # non-excess (normal = 3)

    # Mertens' (2002) standard error of the Sharpe ratio estimator, adjusted
    # for skew/kurtosis — this term appears twice: once (undivided) as the
    # z-stat denominator below, and once (divided by sqrt(n-1)) as the
    # per-trial SR standard error used to estimate the expected max SR you'd
    # see from `num_trials` independent draws of pure noise.
    denom = np.sqrt(max(1e-9, 1 - skew * sharpe + ((kurt - 1) / 4) * sharpe**2))
    sr_std_error = denom / np.sqrt(n - 1)

    # Expected maximum Sharpe ratio across `num_trials` independent trials
    # under the null that the true Sharpe is 0 (Bailey & Lopez de Prado 2014).
    # A previous version of this function hardcoded the per-trial SR
    # variance to 1.0 instead of deriving it from the sample — that made the
    # "expected max under luck" threshold nearly constant regardless of
    # sample size, so a strong, well-sampled edge could score DSR≈0 (i.e.
    # "just luck") purely because the formula never actually reflected how
    # much data backed the estimate.
    euler_gamma = 0.5772156649
    if num_trials > 1:
        expected_max_sr = sr_std_error * (
            (1 - euler_gamma) * stats.norm.ppf(1 - 1.0 / num_trials)
            + euler_gamma * stats.norm.ppf(1 - 1.0 / (num_trials * np.e))
        )
    else:
        expected_max_sr = benchmark_sharpe

    dsr_stat = (sharpe - expected_max_sr) * np.sqrt(n - 1) / denom
    dsr = float(stats.norm.cdf(dsr_stat))

    return {
        "sharpe_ratio": round(float(sharpe), 3),
        "expected_max_sharpe_under_luck": round(float(expected_max_sr), 3),
        "skewness": round(skew, 3),
        "kurtosis": round(kurt, 3),
        "num_trials_assumed": num_trials,
        "deflated_sharpe": round(dsr, 4),
        "interpretation": (
            "DSR > 0.95 通常視為統計上站得住腳" if dsr > 0.95
            else ("DSR > 0.5 方向正確但證據薄弱" if dsr > 0.5 else "DSR <= 0.5，觀察到的績效很可能只是多次嘗試下的運氣")
        ),
    }
