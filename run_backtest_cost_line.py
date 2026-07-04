"""Backtest the institutional cost-line entry signal (src/backtest/
cost_line_signal.py) — free-tier data only, no 分點/Sponsor dependency.

Reads entirely from the local SQLite cache. Does not call FinMind, so it's
parallelized across stocks like run_backtest_composite.py. Bakes in the two
rigor lessons learned from the broker-branch signal work: FDR correction
across the watchlist, AND a split-sample stability check (3 chronological
terciles) computed up front rather than as an afterthought — a result that
passes FDR but is only real in one lucky stretch is graded B, not A (see
src/backtest/credibility.py's `stable_across_periods` parameter).

Usage:
    python run_backtest_cost_line.py [--days N] [--workers N]
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

from src.backtest import backtest, cost_line_signal, credibility, significance
from src.storage import db

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config" / "stocks.yaml"
UNIVERSE_PATH = Path(__file__).parent / "config" / "universe.yaml"
CREDIBILITY_PATH = Path(__file__).parent / "config" / "signal_credibility_cost_line.yaml"
REPORTS_DIR = Path(__file__).parent / "reports"
N_TERCILES = 3
MIN_TERCILE_SIGNALS = 5


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def load_stock_list(use_universe: bool) -> list[dict]:
    """The watchlist (config/stocks.yaml) is the user's own 38-stock
    curated list used for the daily report; config/universe.yaml is the
    full ~2156-stock TWSE/TPEx ordinary-share market, used only for
    backtesting at scale (more statistical power for FDR, and a genuine
    check on whether a finding like 3088's generalizes or was luck)."""
    if use_universe:
        return yaml.safe_load(UNIVERSE_PATH.read_text(encoding="utf-8"))["stocks"]
    return load_config()["stocks"]


def load_from_cache(conn: sqlite3.Connection, sid: str, start_date: str, end_date: str):
    price_df = pd.read_sql(
        "SELECT * FROM stock_price WHERE stock_id=? AND date BETWEEN ? AND ? ORDER BY date",
        conn, params=(sid, start_date, end_date),
    )
    inst_df = pd.read_sql(
        "SELECT * FROM institutional WHERE stock_id=? AND date BETWEEN ? AND ? ORDER BY date",
        conn, params=(sid, start_date, end_date),
    )
    return price_df, inst_df


def _tercile_stability(price_df: pd.DataFrame, inst_df: pd.DataFrame, lookback: int, config: dict) -> bool | None:
    """Splits price/inst data into N_TERCILES chronological segments and
    checks whether the 20-day edge is consistently non-negative wherever
    there's enough signal to judge. See cost_line_signal.py / credibility.py."""
    n = len(price_df)
    tercile_size = n // N_TERCILES
    evaluable_edges = []

    for i in range(N_TERCILES):
        start_i = i * tercile_size
        end_i = n if i == N_TERCILES - 1 else (i + 1) * tercile_size
        seg_price = price_df.iloc[start_i:end_i].reset_index(drop=True)
        seg_dates = set(seg_price["date"])
        seg_inst = inst_df[inst_df["date"].isin(seg_dates)].reset_index(drop=True)

        signals = cost_line_signal.signal_dates(seg_price, seg_inst, lookback, config)
        if len(signals) < MIN_TERCILE_SIGNALS:
            continue
        all_dates = set(seg_price["date"])
        sig_res = backtest.run(seg_price, signals, [20]).get(20, {})
        base_res = backtest.run(seg_price, all_dates, [20]).get(20, {})
        if sig_res.get("sample_count", 0) < MIN_TERCILE_SIGNALS or base_res.get("sample_count", 0) == 0:
            continue
        evaluable_edges.append(sig_res["win_rate_pct"] - base_res["win_rate_pct"])

    if len(evaluable_edges) < 2:
        return None
    return all(e >= -1 for e in evaluable_edges)


def _process_stock(args: tuple) -> dict:
    sid, start_str, end_str, lookback, config, holding_days_list = args
    t0 = time.time()
    conn = db.get_connection()
    try:
        price_df, inst_df = load_from_cache(conn, sid, start_str, end_str)
        if price_df.empty or inst_df.empty:
            return {"sid": sid, "error": "no cached price/institutional data"}

        signals = cost_line_signal.signal_dates(price_df, inst_df, lookback, config)
        all_dates = set(price_df["date"])

        results = backtest.run(price_df, signals, holding_days_list)
        baseline = backtest.run(price_df, all_dates, holding_days_list)

        sig_trades_20d = backtest.trades_for_holding(price_df, signals, 20)
        base_trades_20d = backtest.trades_for_holding(price_df, all_dates, 20)
        mw = significance.mann_whitney_test(
            [t["return_pct"] for t in sig_trades_20d], [t["return_pct"] for t in base_trades_20d]
        )

        stable = _tercile_stability(price_df, inst_df, lookback, config)

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
            "stable": stable,
        }
    except Exception as exc:
        return {"sid": sid, "error": str(exc)}
    finally:
        conn.close()


def _rows(results: dict) -> list[str]:
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


def _edge_rows(signal: dict, baseline: dict) -> list[str]:
    out = []
    for h in signal:
        s, b = signal[h], baseline.get(h, {})
        if s.get("sample_count", 0) == 0 or b.get("sample_count", 0) == 0:
            out.append(f"| {h} | - | - |")
            continue
        out.append(f"| {h} | {s['win_rate_pct']-b['win_rate_pct']:+.1f}pp | {s['avg_return_pct']-b['avg_return_pct']:+.2f}pp |")
    return out


def render_report(per_stock, pooled, baseline_per_stock, baseline_pooled, grades, run_date, cost_cfg) -> str:
    lines = [
        f"# 法人成本線訊號回測報告 — {run_date}",
        "",
        f"訊號定義：`cost_line_signal.signal_dates()` — 收盤價乖離{cost_cfg['investor']}成本線 "
        f"{cost_cfg['entry_min_deviation_pct']}%~{cost_cfg['entry_max_deviation_pct']}%，"
        f"且成本線相較{cost_cfg['cost_trend_window_days']}天前仍在上升（法人持續加碼）。免費資料，不依賴分點/Sponsor。",
        "",
        "## 全部股票合併結果",
        "### 訊號進場",
        "| 持有天數 | 樣本數 | 勝率 | 平均報酬 | 中位數報酬 | 最大回撤 |",
        "|---|---|---|---|---|---|",
        *_rows(pooled),
        "",
        "### 基準線（每日進場）",
        "| 持有天數 | 樣本數 | 勝率 | 平均報酬 | 中位數報酬 | 最大回撤 |",
        "|---|---|---|---|---|---|",
        *_rows(baseline_pooled),
        "",
        "### 訊號相對基準線的優勢",
        "| 持有天數 | 勝率差 | 平均報酬差 |",
        "|---|---|---|",
        *_edge_rows(pooled, baseline_pooled),
        "",
        "## 可信度分級（含FDR校正與分段穩健性檢查）",
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
        lines += _rows(results)
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1095)
    parser.add_argument("--workers", type=int, default=None)
    parser.add_argument("--universe", action="store_true", help="backtest the full ~2156-stock market (config/universe.yaml) instead of the 38-stock watchlist")
    args = parser.parse_args()

    config = load_config()
    cost_cfg = config["cost_line"]
    lookback = config["lookback"]["long"]
    holding_days_list = config["backtest"]["holding_days"]

    end_date = datetime.today()
    start_date = end_date - timedelta(days=args.days)
    end_str, start_str = end_date.strftime("%Y-%m-%d"), start_date.strftime("%Y-%m-%d")

    stocks = load_stock_list(args.universe)
    workers = args.workers or os.cpu_count()
    logger.info("Running %d stocks across %d worker processes (investor=%s)", len(stocks), workers, cost_cfg["investor"])

    tasks = [(stock["code"], start_str, end_str, lookback, config, holding_days_list) for stock in stocks]

    stock_signal_pairs, stock_baseline_pairs = [], []
    per_stock_results, per_stock_baseline = {}, {}
    per_stock_pvalue: dict[str, float] = {}
    per_stock_stable: dict[str, bool | None] = {}

    done = 0
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
            per_stock_stable[sid] = r["stable"]
            logger.info(
                "[%d/%d] %s: %d cost-line signals found, stable=%s (%.1fs)",
                done, len(stocks), sid, r["n_signals"], r["stable"], r["elapsed"],
            )

    if not per_stock_results:
        logger.error("No stocks produced results.")
        sys.exit(1)

    pooled = backtest.run_multi(stock_signal_pairs, holding_days_list)
    baseline_pooled = backtest.run_multi(stock_baseline_pairs, holding_days_list)

    bh_results = significance.benjamini_hochberg(per_stock_pvalue, fdr=0.10) if per_stock_pvalue else {}

    grades = {}
    for sid, results in per_stock_results.items():
        baseline = per_stock_baseline[sid]
        fdr_significant = bh_results.get(sid, {}).get("significant")
        stable = per_stock_stable.get(sid)
        if 10 in results and 20 in results:
            grades[sid] = credibility.grade(results[10], baseline[10], results[20], baseline[20], fdr_significant, stable)
        else:
            grades[sid] = {"grade": "N/A", "reason": "缺少10日或20日持有期資料"}
        if sid in per_stock_pvalue:
            grades[sid]["fdr_p_value"] = round(per_stock_pvalue[sid], 4)
            grades[sid]["fdr_significant"] = fdr_significant
        grades[sid]["stable_across_periods"] = stable

    run_date = end_date.strftime("%Y-%m-%d")
    suffix = "_universe" if args.universe else ""
    credibility_path = CREDIBILITY_PATH.with_name(f"{CREDIBILITY_PATH.stem}{suffix}.yaml")
    credibility_path.write_text(
        yaml.safe_dump(
            {"generated_from_backtest_date": run_date, "backtest_window_days": args.days,
             "cost_line_config": cost_cfg, "stocks": grades},
            allow_unicode=True, sort_keys=False,
        ),
        encoding="utf-8",
    )
    logger.info("Signal credibility grades written to %s", credibility_path)

    report = render_report(per_stock_results, pooled, per_stock_baseline, baseline_pooled, grades, run_date, cost_cfg)
    out_path = REPORTS_DIR / f"backtest_{run_date}_cost_line{suffix}.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
