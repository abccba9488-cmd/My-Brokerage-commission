"""Backtest the composite BUY signal (entry_exit_signal.evaluate_entry,
>=4/5 conditions) instead of the raw broker buy-streak alone.

Reads entirely from the local SQLite cache (data/chips.db) — assumes
run_backtest.py has already been run so price/institutional/broker history
is populated. Does not call FinMind at all.

Usage:
    python run_backtest_composite.py [--days N]
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from src.backtest import backtest, composite_signal, credibility
from src.storage import db

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config" / "stocks.yaml"
CREDIBILITY_PATH = Path(__file__).parent / "config" / "signal_credibility_composite.yaml"
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


def render_report(per_stock: dict, pooled: dict, baseline_per_stock: dict, baseline_pooled: dict, grades: dict, run_date: str) -> str:
    def rows(results: dict) -> list[str]:
        out = []
        for h, r in results.items():
            if r.get("sample_count", 0) == 0:
                out.append(f"| {h} | 0 | - | - | - | - |")
            else:
                out.append(
                    f"| {h} | {r['sample_count']} | {r['win_rate_pct']}% | "
                    f"{r['avg_return_pct']:+.2f}% | {r['median_return_pct']:+.2f}% | {r['max_drawdown_pct']:.2f}% |"
                )
        return out

    def edge_rows(signal: dict, baseline: dict) -> list[str]:
        out = []
        for h in signal:
            s, b = signal[h], baseline.get(h, {})
            if s.get("sample_count", 0) == 0 or b.get("sample_count", 0) == 0:
                out.append(f"| {h} | - | - |")
                continue
            out.append(f"| {h} | {s['win_rate_pct']-b['win_rate_pct']:+.1f}pp | {s['avg_return_pct']-b['avg_return_pct']:+.2f}pp |")
        return out

    lines = [
        f"# 複合訊號（>=4/5條件 BUY）回測報告 — {run_date}",
        "",
        "訊號定義：`entry_exit_signal.evaluate_entry()` 的 BUY 判斷（分點連續買超＋集中度＋法人同步＋成本位置＋量能，"
        "5項至少符合4項），對照「分點連續買超」單一條件的回測結果，看複合條件是否更有鑑別度。",
        "",
        "## 全部股票合併結果",
        "### 訊號進場",
        "| 持有天數 | 樣本數 | 勝率 | 平均報酬 | 中位數報酬 | 最大回撤 |",
        "|---|---|---|---|---|---|",
        *rows(pooled),
        "",
        "### 基準線（每日進場）",
        "| 持有天數 | 樣本數 | 勝率 | 平均報酬 | 中位數報酬 | 最大回撤 |",
        "|---|---|---|---|---|---|",
        *rows(baseline_pooled),
        "",
        "### 訊號相對基準線的優勢",
        "| 持有天數 | 勝率差 | 平均報酬差 |",
        "|---|---|---|",
        *edge_rows(pooled, baseline_pooled),
        "",
        "## 可信度分級",
        "| 股票 | 等級 | 說明 |",
        "|---|---|---|",
    ]
    for sid, g in grades.items():
        lines.append(f"| {sid} | **{g['grade']}** | {g['reason']} |")

    lines += ["", "## 個股明細", ""]
    for sid, results in per_stock.items():
        baseline = baseline_per_stock.get(sid, {})
        g = grades.get(sid, {"grade": "N/A", "reason": ""})
        lines.append(f"### {sid} — 可信度：{g['grade']}")
        lines.append(g["reason"])
        lines.append("")
        lines.append("訊號進場：")
        lines.append("| 持有天數 | 樣本數 | 勝率 | 平均報酬 | 中位數報酬 | 最大回撤 |")
        lines.append("|---|---|---|---|---|---|")
        lines += rows(results)
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365)
    args = parser.parse_args()

    config = load_config()
    lookback = config["lookback"]["long"]
    holding_days_list = config["backtest"]["holding_days"]

    end_date = datetime.today()
    start_date = end_date - timedelta(days=args.days)
    end_str, start_str = end_date.strftime("%Y-%m-%d"), start_date.strftime("%Y-%m-%d")

    conn = db.get_connection()

    stock_signal_pairs, stock_baseline_pairs = [], []
    per_stock_results, per_stock_baseline = {}, {}

    stocks = config["stocks"]
    for idx, stock in enumerate(stocks, 1):
        sid = stock["code"]
        t0 = time.time()
        try:
            price_df, inst_df, broker_df = load_from_cache(conn, sid, start_str, end_str)
            if price_df.empty:
                logger.warning("%s: no cached price data, skipping", sid)
                continue

            signals = composite_signal.signal_dates(price_df, inst_df, broker_df, lookback, config)
            all_dates = set(price_df["date"])

            stock_signal_pairs.append((price_df, signals))
            stock_baseline_pairs.append((price_df, all_dates))
            per_stock_results[sid] = backtest.run(price_df, signals, holding_days_list)
            per_stock_baseline[sid] = backtest.run(price_df, all_dates, holding_days_list)

            logger.info(
                "[%d/%d] %s: %d composite BUY signals found (%.1fs)",
                idx, len(stocks), sid, len(signals), time.time() - t0,
            )
        except Exception as exc:
            logger.error("%s: failed, skipping — %s", sid, exc)
            continue

    conn.close()

    if not per_stock_results:
        logger.error("No stocks produced results.")
        sys.exit(1)

    pooled = backtest.run_multi(stock_signal_pairs, holding_days_list)
    baseline_pooled = backtest.run_multi(stock_baseline_pairs, holding_days_list)

    grades = {}
    for sid, results in per_stock_results.items():
        baseline = per_stock_baseline[sid]
        if 10 in results and 20 in results:
            grades[sid] = credibility.grade(results[10], baseline[10], results[20], baseline[20])
        else:
            grades[sid] = {"grade": "N/A", "reason": "缺少10日或20日持有期資料"}

    run_date = end_date.strftime("%Y-%m-%d")
    CREDIBILITY_PATH.write_text(
        yaml.safe_dump(
            {"generated_from_backtest_date": run_date, "backtest_window_days": args.days,
             "stocks": grades},
            allow_unicode=True, sort_keys=False,
        ),
        encoding="utf-8",
    )

    report = render_report(per_stock_results, pooled, per_stock_baseline, baseline_pooled, grades, run_date)
    out_path = REPORTS_DIR / f"backtest_{run_date}_composite.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
