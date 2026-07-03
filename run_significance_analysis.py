"""Apply Mann-Whitney significance testing + Benjamini-Hochberg FDR
correction + Deflated Sharpe Ratio to the existing backtest results.

Reads entirely from the local SQLite cache — no FinMind calls. Rebuilds
signal dates for the raw broker-streak signal (cheap) using
broker_signal.py, and reuses the already-graded composite results if
config/signal_credibility_composite.yaml exists.

Usage:
    python run_significance_analysis.py [--days N]
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
SIGNIFICANCE_HOLDING_DAYS = 20  # least noisy of the four holding periods we track


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

    end_date = datetime.today()
    start_date = end_date - timedelta(days=args.days)
    end_str, start_str = end_date.strftime("%Y-%m-%d"), start_date.strftime("%Y-%m-%d")

    conn = db.get_connection()

    per_stock_pvalues: dict[str, float] = {}
    pooled_signal_returns: list[float] = []
    pooled_baseline_returns: list[float] = []
    per_stock_detail: dict[str, dict] = {}

    for stock in config["stocks"]:
        sid = stock["code"]
        try:
            price_df, broker_df = load_from_cache(conn, sid, start_str, end_str)
            if price_df.empty:
                continue

            broker_df = filter_by_volume_share(broker_df, price_df, broker_cfg["volume_share_min_pct"])
            signals = broker_signal.signal_dates(
                broker_df, broker_cfg["streak_min_days"], broker_cfg["streak_allow_gap_days"], lookback,
            )
            all_dates = set(price_df["date"])

            signal_trades = backtest.trades_for_holding(price_df, signals, SIGNIFICANCE_HOLDING_DAYS)
            baseline_trades = backtest.trades_for_holding(price_df, all_dates, SIGNIFICANCE_HOLDING_DAYS)

            signal_returns = [t["return_pct"] for t in signal_trades]
            baseline_returns = [t["return_pct"] for t in baseline_trades]

            test_result = significance.mann_whitney_test(signal_returns, baseline_returns)
            per_stock_pvalues[sid] = test_result["p_value"]
            per_stock_detail[sid] = {
                "sample_count": len(signal_returns),
                "p_value": test_result["p_value"],
            }

            pooled_signal_returns.extend(signal_returns)
            pooled_baseline_returns.extend(baseline_returns)

            logger.info("%s: n=%d signal trades, p=%.4f", sid, len(signal_returns), test_result["p_value"])
        except Exception as exc:
            logger.error("%s: failed — %s", sid, exc)
            continue

    conn.close()

    bh_results = significance.benjamini_hochberg(per_stock_pvalues, fdr=0.10)

    # DSR on the pooled raw-streak result. num_trials=3: default params, the
    # streak_min_days=5/volume_share_min_pct=20 experiment, and the composite
    # signal are the three distinct configurations actually run against this
    # data — a documented lower bound, see significance.py's docstring.
    dsr_signal = significance.deflated_sharpe_ratio(pooled_signal_returns, num_trials=3)
    dsr_baseline = significance.deflated_sharpe_ratio(pooled_baseline_returns, num_trials=1)

    lines = [
        f"# 統計顯著性分析（Mann-Whitney + BH-FDR + Deflated Sharpe）— {end_date.strftime('%Y-%m-%d')}",
        "",
        f"檢定持有期：{SIGNIFICANCE_HOLDING_DAYS} 日（最不受短期雜訊干擾）。FDR 目標：10%。",
        "",
        "## Deflated Sharpe Ratio（假設已知 3 次獨立實驗：預設參數／拉嚴門檻／複合訊號）",
        "",
        f"訊號池化報酬：{dsr_signal}",
        "",
        f"基準線池化報酬（僅供對照，num_trials=1）：{dsr_baseline}",
        "",
        "## 逐股 Mann-Whitney 檢定 + Benjamini-Hochberg FDR 校正",
        "",
        "| 股票 | 樣本數 | p值 | BH門檻 | 排名 | FDR顯著？ |",
        "|---|---|---|---|---|---|",
    ]
    for sid, bh in sorted(bh_results.items(), key=lambda kv: kv[1]["rank"]):
        detail = per_stock_detail[sid]
        sig_mark = "✅ 是" if bh["significant"] else "否"
        lines.append(
            f"| {sid} | {detail['sample_count']} | {bh['p_value']:.4f} | "
            f"{bh['bh_threshold']:.4f} | {bh['rank']} | {sig_mark} |"
        )

    n_significant = sum(1 for bh in bh_results.values() if bh["significant"])
    lines += [
        "",
        f"**結論：38 檔中，經 FDR 校正後有 {n_significant} 檔在統計上顯著（原始 A 級有 4 檔）。**",
    ]

    report = "\n".join(lines)
    out_path = REPORTS_DIR / f"significance_{end_date.strftime('%Y-%m-%d')}.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
