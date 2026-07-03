"""Walk-forward backtest of the continuous momentum ranking signal
(src/backtest/momentum_signal.py) — fits z-score normalization on each
fold's train period, scores the test period out-of-sample, and reports
pooled results vs. the random-entry baseline.

Reads entirely from the local SQLite cache. Usage:
    python run_backtest_momentum.py [--days N] [--top-pct 0.2]
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from src.backtest import backtest, momentum_signal, walk_forward
from src.storage import db

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config" / "stocks.yaml"
REPORTS_DIR = Path(__file__).parent / "reports"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def load_from_cache(conn: sqlite3.Connection, sid: str, start_date: str, end_date: str):
    price_df = pd.read_sql(
        "SELECT * FROM stock_price WHERE stock_id=? AND date BETWEEN ? AND ? ORDER BY date",
        conn, params=(sid, start_date, end_date),
    )
    inst_df = pd.read_sql(
        "SELECT * FROM institutional WHERE stock_id=? AND date BETWEEN ? AND ? ORDER BY date",
        conn, params=(sid, start_date, end_date),
    )
    broker_df = pd.read_sql(
        "SELECT * FROM broker_trade WHERE stock_id=? AND date BETWEEN ? AND ?",
        conn, params=(sid, start_date, end_date),
    )
    return price_df, inst_df, broker_df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    parser.add_argument("--top-pct", type=float, default=0.2, help="fraction of days flagged as signal, by rank")
    parser.add_argument("--train-months", type=int, default=9)
    parser.add_argument("--test-months", type=int, default=3)
    parser.add_argument("--step-months", type=int, default=3)
    args = parser.parse_args()

    config = load_config()
    broker_cfg = config["broker"]
    lookback = config["lookback"]["long"]
    holding_days_list = config["backtest"]["holding_days"]

    end_date = datetime.today()
    start_date = end_date - timedelta(days=args.days)
    start_str, end_str = start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d")

    conn = db.get_connection()

    signal_pairs, baseline_pairs = [], []
    fold_count_used = 0

    for stock in config["stocks"]:
        sid = stock["code"]
        try:
            price_df, inst_df, broker_df = load_from_cache(conn, sid, start_str, end_str)
            if price_df.empty or len(price_df) < 60:
                continue

            folds = walk_forward.generate_folds(
                price_df["date"].tolist(), args.train_months, args.test_months, args.step_months
            )
            if not folds:
                logger.warning("%s: not enough history for even one walk-forward fold, skipping", sid)
                continue
            fold_count_used = max(fold_count_used, len(folds))

            raw_features = momentum_signal.compute_raw_features(
                price_df, inst_df, broker_df, lookback, broker_cfg["volume_share_min_pct"]
            )

            for fold in folds:
                train_raw = walk_forward.split_by_date(raw_features, fold["train_start"], fold["train_end"])
                test_raw = walk_forward.split_by_date(raw_features, fold["test_start"], fold["test_end"])
                test_price = walk_forward.split_by_date(price_df, fold["test_start"], fold["test_end"])
                if train_raw.empty or test_raw.empty or test_price.empty:
                    continue

                fit_stats = momentum_signal.fit_normalization(train_raw)
                train_scores = momentum_signal.score_with_fitted_stats(train_raw, fit_stats)
                threshold = train_scores.quantile(1 - args.top_pct)  # threshold fit on TRAIN score distribution

                test_scores = momentum_signal.score_with_fitted_stats(test_raw, fit_stats)
                test_raw = test_raw.assign(score=test_scores)
                signals = momentum_signal.signal_dates_from_score(test_raw, "score", threshold)

                all_dates = set(test_price["date"])
                signal_pairs.append((test_price, signals))
                baseline_pairs.append((test_price, all_dates))

            logger.info("%s: %d walk-forward fold(s) processed", sid, len(folds))
        except Exception as exc:
            logger.error("%s: failed — %s", sid, exc)
            continue

    conn.close()

    if not signal_pairs:
        logger.error("No results produced.")
        sys.exit(1)

    pooled = backtest.run_multi(signal_pairs, holding_days_list)
    baseline_pooled = backtest.run_multi(baseline_pairs, holding_days_list)

    lines = [
        f"# 連續型評分訊號 Walk-Forward 回測 — {end_date.strftime('%Y-%m-%d')}",
        "",
        f"每檔股票 {fold_count_used} 折（train={args.train_months}月／test={args.test_months}月／step={args.step_months}月），"
        f"z-score 正規化與門檻都只用訓練期資料校準，測試期完全樣本外。取分數前 {args.top_pct*100:.0f}% 的日子視為訊號。",
    ]
    if fold_count_used <= 1:
        lines.append(
            "\n**注意：目前只有1折（資料範圍不夠多折 walk-forward），這只是誠實的樣本外測試，"
            "不是真正的 walk-forward 穩健性驗證——等擴充歷史跑完會有更多折可用。**"
        )
    lines += [
        "",
        "## 訊號進場（測試期，池化全部股票全部折）",
        "| 持有天數 | 樣本數 | 勝率 | 平均報酬 | 中位數報酬 | 最大回撤 |",
        "|---|---|---|---|---|---|",
    ]
    for h, r in pooled.items():
        if r.get("sample_count", 0) == 0:
            lines.append(f"| {h} | 0 | - | - | - | - |")
        else:
            lines.append(f"| {h} | {r['sample_count']} | {r['win_rate_pct']}% | {r['avg_return_pct']:+.2f}% | {r['median_return_pct']:+.2f}% | {r['max_drawdown_pct']:.2f}% |")

    lines += ["", "## 基準線（測試期，每日進場）", "| 持有天數 | 樣本數 | 勝率 | 平均報酬 | 中位數報酬 | 最大回撤 |", "|---|---|---|---|---|---|"]
    for h, r in baseline_pooled.items():
        if r.get("sample_count", 0) == 0:
            lines.append(f"| {h} | 0 | - | - | - | - |")
        else:
            lines.append(f"| {h} | {r['sample_count']} | {r['win_rate_pct']}% | {r['avg_return_pct']:+.2f}% | {r['median_return_pct']:+.2f}% | {r['max_drawdown_pct']:.2f}% |")

    lines += ["", "## 訊號相對基準線優勢", "| 持有天數 | 勝率差 | 平均報酬差 |", "|---|---|---|"]
    for h in holding_days_list:
        s, b = pooled.get(h, {}), baseline_pooled.get(h, {})
        if s.get("sample_count", 0) == 0 or b.get("sample_count", 0) == 0:
            lines.append(f"| {h} | - | - |")
        else:
            lines.append(f"| {h} | {s['win_rate_pct']-b['win_rate_pct']:+.1f}pp | {s['avg_return_pct']-b['avg_return_pct']:+.2f}pp |")

    report = "\n".join(lines)
    out_path = REPORTS_DIR / f"momentum_walkforward_{end_date.strftime('%Y-%m-%d')}.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
