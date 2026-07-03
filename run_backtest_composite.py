"""Backtest the composite BUY signal (entry_exit_signal.evaluate_entry,
>=4/5 conditions) instead of the raw broker buy-streak alone.

Reads entirely from the local SQLite cache (data/chips.db) — assumes
run_backtest.py has already been run so price/institutional/broker history
is populated. Does not call FinMind at all, which is what makes it safe to
parallelize across stocks: each worker process opens its own read-only
connection (SQLite WAL mode supports concurrent readers), so there's no
API rate-limit risk like there is with the live-fetch backtest scripts.

Usage:
    python run_backtest_composite.py [--days N] [--workers N]
"""
from __future__ import annotations

import argparse
import logging
import os
import sqlite3
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from src.backtest import backtest, composite_signal, credibility, significance
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


def _process_stock(args: tuple) -> dict:
    """Runs in a worker process — opens its own DB connection (sqlite3
    connections aren't fork/pickle-safe, so each process needs its own)."""
    sid, start_str, end_str, lookback, config, holding_days_list = args
    t0 = time.time()
    conn = db.get_connection()
    try:
        price_df, inst_df, broker_df = load_from_cache(conn, sid, start_str, end_str)
        if price_df.empty:
            return {"sid": sid, "error": "no cached price data"}

        signals = composite_signal.signal_dates(price_df, inst_df, broker_df, lookback, config)
        all_dates = set(price_df["date"])

        results = backtest.run(price_df, signals, holding_days_list)
        baseline = backtest.run(price_df, all_dates, holding_days_list)

        # Mann-Whitney at 20-day holding, feeding cross-stock FDR correction below —
        # same rigor as run_backtest.py's single-condition test (see credibility.grade).
        sig_trades_20d = backtest.trades_for_holding(price_df, signals, 20)
        base_trades_20d = backtest.trades_for_holding(price_df, all_dates, 20)
        mw = significance.mann_whitney_test(
            [t["return_pct"] for t in sig_trades_20d], [t["return_pct"] for t in base_trades_20d]
        )

        return {
            "sid": sid,
            "price_df": price_df,
            "signals": signals,
            "all_dates": all_dates,
            "results": results,
            "baseline": baseline,
            "n_signals": len(signals),
            "elapsed": time.time() - t0,
            "p_value": mw["p_value"],
        }
    except Exception as exc:
        return {"sid": sid, "error": str(exc)}
    finally:
        conn.close()


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
    parser.add_argument(
        "--workers", type=int, default=None,
        help="parallel worker processes (default: all logical CPUs) — safe because this script never calls FinMind",
    )
    args = parser.parse_args()

    config = load_config()
    lookback = config["lookback"]["long"]
    holding_days_list = config["backtest"]["holding_days"]

    end_date = datetime.today()
    start_date = end_date - timedelta(days=args.days)
    end_str, start_str = end_date.strftime("%Y-%m-%d"), start_date.strftime("%Y-%m-%d")

    stocks = config["stocks"]
    workers = args.workers or os.cpu_count()
    logger.info("Running %d stocks across %d worker processes", len(stocks), workers)

    stock_signal_pairs, stock_baseline_pairs = [], []
    per_stock_results, per_stock_baseline = {}, {}

    tasks = [(stock["code"], start_str, end_str, lookback, config, holding_days_list) for stock in stocks]

    done = 0
    per_stock_pvalue: dict[str, float] = {}
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process_stock, t): t[0] for t in tasks}
        for fut in as_completed(futures):
            sid = futures[fut]
            r = fut.result()
            done += 1
            if "error" in r:
                logger.error("[%d/%d] %s: failed, skipping — %s", done, len(stocks), sid, r["error"])
                continue
            stock_signal_pairs.append((r["price_df"], r["signals"]))
            stock_baseline_pairs.append((r["price_df"], r["all_dates"]))
            per_stock_results[sid] = r["results"]
            per_stock_baseline[sid] = r["baseline"]
            per_stock_pvalue[sid] = r["p_value"]
            logger.info("[%d/%d] %s: %d composite BUY signals found (%.1fs)", done, len(stocks), sid, r["n_signals"], r["elapsed"])

    if not per_stock_results:
        logger.error("No stocks produced results.")
        sys.exit(1)

    pooled = backtest.run_multi(stock_signal_pairs, holding_days_list)
    baseline_pooled = backtest.run_multi(stock_baseline_pairs, holding_days_list)

    # FDR=10%, same exploratory-screen rationale as run_backtest.py (see significance.py).
    bh_results = significance.benjamini_hochberg(per_stock_pvalue, fdr=0.10) if per_stock_pvalue else {}

    grades = {}
    for sid, results in per_stock_results.items():
        baseline = per_stock_baseline[sid]
        fdr_significant = bh_results.get(sid, {}).get("significant")
        if 10 in results and 20 in results:
            grades[sid] = credibility.grade(results[10], baseline[10], results[20], baseline[20], fdr_significant)
        else:
            grades[sid] = {"grade": "N/A", "reason": "缺少10日或20日持有期資料"}
        if sid in per_stock_pvalue:
            grades[sid]["fdr_p_value"] = round(per_stock_pvalue[sid], 4)
            grades[sid]["fdr_significant"] = fdr_significant

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
