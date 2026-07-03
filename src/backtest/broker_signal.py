"""Roll broker_streak's buy-streak detection across every historical day.

broker_streak.compute() only reports the streak as of the *last* day in
whatever slice of broker_df you hand it — that's fine for run_daily.py
(today's snapshot), but backtesting needs to know, for every past day,
whether a qualifying buy-streak existed as of that day. This reimplements
the same walk-backward-with-gap-tolerance algorithm from broker_streak.py,
applied at every day instead of just the last one, and caps the lookback
the same way run_daily.py windows broker_df in production.
"""
from __future__ import annotations

from src.indicators.broker_streak import daily_net


def signal_dates(broker_df, streak_min_days: int, allow_gap_days: int, lookback_days: int) -> set[str]:
    if broker_df.empty:
        return set()

    daily = daily_net(broker_df)
    dates_out: set[str] = set()

    for _, g in daily.groupby("broker_id"):
        g = g.sort_values("date").reset_index(drop=True)
        nets = g["net"].tolist()
        dates = g["date"].tolist()

        for end in range(len(nets)):
            direction = 1 if nets[end] > 0 else (-1 if nets[end] < 0 else 0)
            if direction <= 0:
                continue  # only care about buy-streaks for this signal

            streak = 0
            gaps_used = 0
            idx = end
            while idx >= 0 and streak < lookback_days:
                net = nets[idx]
                if net > 0:
                    streak += 1
                elif gaps_used < allow_gap_days:
                    gaps_used += 1
                    streak += 1
                else:
                    break
                idx -= 1

            if streak >= streak_min_days:
                dates_out.add(dates[end])

    return dates_out
