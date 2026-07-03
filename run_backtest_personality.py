"""Test whether excluding "隔日沖客" (short-term flipper) brokers from the
buy-streak signal improves the edge, using an honest train/test split.

Classifying broker personality using the SAME period you then backtest the
filtered signal on is look-ahead bias — you wouldn't have known a broker's
historical flip rate yet on day 1 of that period. Instead: first half of
the window trains the personality labels, second half is the out-of-sample
test where the filtered signal is actually backtested.

Reads entirely from the local SQLite cache. Usage:
    python run_backtest_personality.py [--days N]
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

from src.backtest import backtest, broker_signal, significance
from src.indicators.broker_personality import classify_brokers
from src.indicators.broker_streak import filter_by_volume_share
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
    broker_df = pd.read_sql(
        "SELECT * FROM broker_trade WHERE stock_id=? AND date BETWEEN ? AND ?",
        conn, params=(sid, start_date, end_date),
    )
    return price_df, broker_df


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()

    config = load_config()
    broker_cfg = config["broker"]
    lookback = config["lookback"]["long"]
    holding_days_list = config["backtest"]["holding_days"]

    end_date = datetime.today()
    start_date = end_date - timedelta(days=args.days)
    mid_date = start_date + (end_date - start_date) / 2
    start_str, mid_str, end_str = (d.strftime("%Y-%m-%d") for d in (start_date, mid_date, end_date))

    logger.info("Train (personality classification): %s to %s", start_str, mid_str)
    logger.info("Test (out-of-sample backtest): %s to %s", mid_str, end_str)

    conn = db.get_connection()

    filtered_pairs, unfiltered_pairs, baseline_pairs = [], [], []
    n_flippers_total = 0

    for stock in config["stocks"]:
        sid = stock["code"]
        try:
            train_price, train_broker = load_from_cache(conn, sid, start_str, mid_str)
            test_price, test_broker = load_from_cache(conn, sid, mid_str, end_str)
            if train_price.empty or test_price.empty:
                continue

            train_broker_filtered = filter_by_volume_share(train_broker, train_price, broker_cfg["volume_share_min_pct"])
            personalities = classify_brokers(train_broker_filtered, train_price, broker_cfg["volume_share_min_pct"])
            flipper_ids = set(personalities.loc[personalities["label"] == "隔日沖客", "broker_id"]) if not personalities.empty else set()
            n_flippers_total += len(flipper_ids)

            test_broker_filtered = filter_by_volume_share(test_broker, test_price, broker_cfg["volume_share_min_pct"])

            # Unfiltered: normal signal on the test period (apples-to-apples baseline for this split).
            unfiltered_signals = broker_signal.signal_dates(
                test_broker_filtered, broker_cfg["streak_min_days"], broker_cfg["streak_allow_gap_days"], lookback
            )
            # Filtered: same, but with known flippers' rows dropped before streak detection.
            test_broker_no_flippers = test_broker_filtered[~test_broker_filtered["broker_id"].isin(flipper_ids)]
            filtered_signals = broker_signal.signal_dates(
                test_broker_no_flippers, broker_cfg["streak_min_days"], broker_cfg["streak_allow_gap_days"], lookback
            )

            all_dates = set(test_price["date"])
            filtered_pairs.append((test_price, filtered_signals))
            unfiltered_pairs.append((test_price, unfiltered_signals))
            baseline_pairs.append((test_price, all_dates))

            logger.info(
                "%s: %d flippers identified, %d unfiltered signals -> %d filtered signals",
                sid, len(flipper_ids), len(unfiltered_signals), len(filtered_signals),
            )
        except Exception as exc:
            logger.error("%s: failed — %s", sid, exc)
            continue

    conn.close()

    filtered_pooled = backtest.run_multi(filtered_pairs, holding_days_list)
    unfiltered_pooled = backtest.run_multi(unfiltered_pairs, holding_days_list)
    baseline_pooled = backtest.run_multi(baseline_pairs, holding_days_list)

    lines = [
        f"# 分點性格過濾（排除隔日沖客）樣本外驗證 — {end_date.strftime('%Y-%m-%d')}",
        "",
        f"訓練期（分類分點性格）：{start_str} ~ {mid_str}　測試期（樣本外回測）：{mid_str} ~ {end_str}",
        f"總共識別出 {n_flippers_total} 個「隔日沖客」標籤（跨全部股票加總，同分點可能在不同股票重複出現）",
        "",
        "## 三組結果對照（測試期，池化全部股票）",
        "",
        "| 持有天數 | 未過濾訊號優勢 | 排除隔日沖客後優勢 | 未過濾樣本數 | 過濾後樣本數 |",
        "|---|---|---|---|---|",
    ]
    for h in holding_days_list:
        u, f, b = unfiltered_pooled.get(h, {}), filtered_pooled.get(h, {}), baseline_pooled.get(h, {})
        if u.get("sample_count", 0) == 0 or f.get("sample_count", 0) == 0 or b.get("sample_count", 0) == 0:
            lines.append(f"| {h} | - | - | {u.get('sample_count',0)} | {f.get('sample_count',0)} |")
            continue
        u_edge = f"{u['win_rate_pct']-b['win_rate_pct']:+.1f}pp / {u['avg_return_pct']-b['avg_return_pct']:+.2f}pp"
        f_edge = f"{f['win_rate_pct']-b['win_rate_pct']:+.1f}pp / {f['avg_return_pct']-b['avg_return_pct']:+.2f}pp"
        lines.append(f"| {h} | {u_edge} | {f_edge} | {u['sample_count']} | {f['sample_count']} |")

    report = "\n".join(lines)
    out_path = REPORTS_DIR / f"personality_filter_{end_date.strftime('%Y-%m-%d')}.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
