"""Rank-based signal from continuous, z-scored momentum features — the
"accumulate weak signals" alternative to entry_exit_signal's AND-of-booleans
rule engine that the external quant review argued for. Instead of requiring
each condition to individually clear a hard threshold, this sums how many
standard deviations each feature is above ITS OWN history (institutional
buying acceleration, aggregate significant-broker net-buy momentum, volume
ratio) and flags the top `top_pct` of days by that combined score.

Vectorized (no per-day Python loop over broker windows like
composite_signal.py) — one pass of rolling/groupby operations, not a
per-day re-simulation.

Normalization is split from feature computation (fit_normalization /
score_with_fitted_stats) specifically so walk_forward.py can fit the
z-score mean/std on a train period and apply it to a test period, instead
of normalizing over the whole series and leaking test-period statistics
backward into the train fit.
"""
from __future__ import annotations

import pandas as pd

from src.indicators import momentum
from src.indicators.broker_streak import filter_by_volume_share

FEATURE_COLUMNS = ["broker_net_slope", "foreign_net_slope", "trust_net_slope", "volume_ratio"]


def compute_raw_features(
    price_df: pd.DataFrame,
    inst_df: pd.DataFrame,
    broker_df: pd.DataFrame,
    lookback_days: int,
    volume_share_min_pct: float,
) -> pd.DataFrame:
    """One row per date in price_df, with the raw (un-normalized) feature values."""
    price_df = price_df.sort_values("date").reset_index(drop=True)

    volume_ratio = price_df["volume"] / price_df["volume"].rolling(lookback_days, min_periods=1).mean()

    if not broker_df.empty:
        broker_filtered = filter_by_volume_share(broker_df, price_df, volume_share_min_pct)
        daily_net = broker_filtered.groupby("date").apply(
            lambda g: (g["buy_shares"] - g["sell_shares"]).sum(), include_groups=False
        )
        daily_net = daily_net.reindex(price_df["date"]).fillna(0.0)
        broker_net_slope = daily_net.rolling(lookback_days, min_periods=2).apply(momentum._slope, raw=False)
    else:
        broker_net_slope = pd.Series(0.0, index=price_df["date"])

    inst_mom = momentum.institutional_momentum(inst_df, lookback_days) if not inst_df.empty else pd.DataFrame(
        columns=["date", "foreign_net_slope", "trust_net_slope"]
    )

    out = pd.DataFrame({
        "date": price_df["date"].values,
        "volume_ratio": volume_ratio.values,
        "broker_net_slope": broker_net_slope.reindex(price_df["date"]).values,
    })
    out = out.merge(inst_mom[["date", "foreign_net_slope", "trust_net_slope"]], on="date", how="left")
    for col in FEATURE_COLUMNS:
        out[col] = out[col].fillna(0.0)
    return out


def fit_normalization(raw_features: pd.DataFrame) -> dict[str, tuple[float, float]]:
    """mean/std per feature column, computed only over the given (train) rows."""
    return {col: (raw_features[col].mean(), raw_features[col].std()) for col in FEATURE_COLUMNS}


def score_with_fitted_stats(raw_features: pd.DataFrame, fit_stats: dict[str, tuple[float, float]]) -> pd.Series:
    total = pd.Series(0.0, index=raw_features.index)
    for col in FEATURE_COLUMNS:
        mean, std = fit_stats.get(col, (0.0, 0.0))
        z = (raw_features[col] - mean) / std if std and not pd.isna(std) and std != 0 else pd.Series(0.0, index=raw_features.index)
        total = total + z
    return total


def signal_dates_from_score(scored_dates: pd.DataFrame, score_col: str, threshold: float) -> set[str]:
    return set(scored_dates.loc[scored_dates[score_col] >= threshold, "date"])


def signal_dates(
    price_df: pd.DataFrame,
    inst_df: pd.DataFrame,
    broker_df: pd.DataFrame,
    lookback_days: int,
    volume_share_min_pct: float,
    top_pct: float = 0.2,
) -> set[str]:
    """Self-normalizing convenience wrapper (fits and scores on the same
    period) — fine for a single-period backtest, but walk_forward.py fits on
    train and scores test separately instead of calling this directly."""
    raw = compute_raw_features(price_df, inst_df, broker_df, lookback_days, volume_share_min_pct)
    stats = fit_normalization(raw)
    raw["score"] = score_with_fitted_stats(raw, stats)
    if raw["score"].dropna().empty:
        return set()
    threshold = raw["score"].quantile(1 - top_pct)
    return signal_dates_from_score(raw, "score", threshold)
