"""Backtest 3 classic technical indicators (KD低檔黃金交叉, MACD黃金交叉,
布林通道觸及下軌反彈) for swing trading — src/backtest/technical_signal.py.

Price-only, cache-only, no FinMind calls — parallelized across
(stock, indicator) pairs like run_backtest_composite.py /
run_backtest_cost_line.py. Same rigor baked in from the start: FDR
correction and a split-sample stability check per indicator (see
src/backtest/credibility.py's `stable_across_periods`).

Usage:
    python run_backtest_technical.py [--days N] [--workers N]
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

from src.backtest import backtest, credibility, significance, technical_signal
from src.storage import db

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config" / "stocks.yaml"
REPORTS_DIR = Path(__file__).parent / "reports"
N_TERCILES = 3
MIN_TERCILE_SIGNALS = 5

INDICATORS = {
    "kd": technical_signal.kd_signal_dates,
    "macd": technical_signal.macd_signal_dates,
    "bollinger": technical_signal.bollinger_signal_dates,
}

INDICATOR_LABELS = {
    "kd": "KD低檔黃金交叉",
    "macd": "MACD黃金交叉",
    "bollinger": "布林通道觸及下軌反彈",
}


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def load_price(conn: sqlite3.Connection, sid: str, start_date: str, end_date: str) -> pd.DataFrame:
    return pd.read_sql(
        "SELECT * FROM stock_price WHERE stock_id=? AND date BETWEEN ? AND ? ORDER BY date",
        conn, params=(sid, start_date, end_date),
    )


def _tercile_stability(price_df: pd.DataFrame, signal_fn, config: dict) -> bool | None:
    n = len(price_df)
    tercile_size = n // N_TERCILES
    evaluable_edges = []

    for i in range(N_TERCILES):
        start_i = i * tercile_size
        end_i = n if i == N_TERCILES - 1 else (i + 1) * tercile_size
        seg_price = price_df.iloc[start_i:end_i].reset_index(drop=True)

        signals = signal_fn(seg_price, config)
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


def _process(args: tuple) -> dict:
    sid, indicator, start_str, end_str, config, holding_days_list = args
    t0 = time.time()
    conn = db.get_connection()
    try:
        price_df = load_price(conn, sid, start_str, end_str)
        if price_df.empty:
            return {"sid": sid, "indicator": indicator, "error": "no cached price data"}

        signal_fn = INDICATORS[indicator]
        signals = signal_fn(price_df, config)
        all_dates = set(price_df["date"])

        results = backtest.run(price_df, signals, holding_days_list)
        baseline = backtest.run(price_df, all_dates, holding_days_list)

        sig_trades_20d = backtest.trades_for_holding(price_df, signals, 20)
        base_trades_20d = backtest.trades_for_holding(price_df, all_dates, 20)
        mw = significance.mann_whitney_test(
            [t["return_pct"] for t in sig_trades_20d], [t["return_pct"] for t in base_trades_20d]
        )

        stable = _tercile_stability(price_df, signal_fn, config)

        return {
            "sid": sid, "indicator": indicator,
            "price_df": price_df, "signals": signals, "all_dates": all_dates,
            "results": results, "baseline": baseline,
            "n_signals": len(signals), "elapsed": time.time() - t0,
            "p_value": mw["p_value"], "stable": stable,
        }
    except Exception as exc:
        return {"sid": sid, "indicator": indicator, "error": str(exc)}
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


def render_indicator_report(indicator, per_stock, pooled, baseline_per_stock, baseline_pooled, grades, run_date) -> str:
    lines = [
        f"# {INDICATOR_LABELS[indicator]} 波段訊號回測報告 — {run_date}",
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
        lines.append("| 持有天數 | 樣本數 | 勝率 | 平均報酬 | 中位數報酬 | 最大回撤 |")
        lines.append("|---|---|---|---|---|---|")
        lines += _rows(results)
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1095)
    parser.add_argument("--workers", type=int, default=None)
    args = parser.parse_args()

    config = load_config()
    holding_days_list = config["backtest"]["holding_days"]

    end_date = datetime.today()
    start_date = end_date - timedelta(days=args.days)
    end_str, start_str = end_date.strftime("%Y-%m-%d"), start_date.strftime("%Y-%m-%d")

    stocks = config["stocks"]
    workers = args.workers or os.cpu_count()
    logger.info("Running %d stocks x %d indicators across %d worker processes", len(stocks), len(INDICATORS), workers)

    tasks = [
        (stock["code"], indicator, start_str, end_str, config, holding_days_list)
        for stock in stocks for indicator in INDICATORS
    ]

    per_indicator: dict[str, dict] = {
        ind: {"signal_pairs": [], "baseline_pairs": [], "results": {}, "baseline": {}, "pvalue": {}, "stable": {}}
        for ind in INDICATORS
    }

    done = 0
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_process, t): (t[0], t[1]) for t in tasks}
        for fut in as_completed(futures):
            sid, indicator = futures[fut]
            r = fut.result()
            done += 1
            if "error" in r:
                logger.error("[%d/%d] %s/%s: failed — %s", done, len(tasks), sid, indicator, r["error"])
                continue
            bucket = per_indicator[indicator]
            bucket["signal_pairs"].append((r["price_df"], r["signals"]))
            bucket["baseline_pairs"].append((r["price_df"], r["all_dates"]))
            bucket["results"][sid] = r["results"]
            bucket["baseline"][sid] = r["baseline"]
            bucket["pvalue"][sid] = r["p_value"]
            bucket["stable"][sid] = r["stable"]
            logger.info(
                "[%d/%d] %s/%s: %d signals, stable=%s (%.1fs)",
                done, len(tasks), sid, indicator, r["n_signals"], r["stable"], r["elapsed"],
            )

    run_date = end_date.strftime("%Y-%m-%d")

    for indicator, bucket in per_indicator.items():
        if not bucket["results"]:
            logger.error("%s: no stocks produced results", indicator)
            continue

        pooled = backtest.run_multi(bucket["signal_pairs"], holding_days_list)
        baseline_pooled = backtest.run_multi(bucket["baseline_pairs"], holding_days_list)
        bh_results = significance.benjamini_hochberg(bucket["pvalue"], fdr=0.10) if bucket["pvalue"] else {}

        grades = {}
        for sid, results in bucket["results"].items():
            baseline = bucket["baseline"][sid]
            fdr_significant = bh_results.get(sid, {}).get("significant")
            stable = bucket["stable"].get(sid)
            if 10 in results and 20 in results:
                grades[sid] = credibility.grade(results[10], baseline[10], results[20], baseline[20], fdr_significant, stable)
            else:
                grades[sid] = {"grade": "N/A", "reason": "缺少10日或20日持有期資料"}
            if sid in bucket["pvalue"]:
                grades[sid]["fdr_p_value"] = round(bucket["pvalue"][sid], 4)
                grades[sid]["fdr_significant"] = fdr_significant
            grades[sid]["stable_across_periods"] = stable

        credibility_path = Path(__file__).parent / "config" / f"signal_credibility_technical_{indicator}.yaml"
        credibility_path.write_text(
            yaml.safe_dump(
                {"generated_from_backtest_date": run_date, "backtest_window_days": args.days,
                 "indicator": indicator, "stocks": grades},
                allow_unicode=True, sort_keys=False,
            ),
            encoding="utf-8",
        )

        report = render_indicator_report(indicator, bucket["results"], pooled, bucket["baseline"], baseline_pooled, grades, run_date)
        out_path = REPORTS_DIR / f"backtest_{run_date}_technical_{indicator}.md"
        out_path.write_text(report, encoding="utf-8")
        logger.info("%s: saved %s", indicator, out_path)

        a_count = sum(1 for g in grades.values() if g["grade"] == "A")
        logger.info(
            "%s pooled 20d edge: win %+0.1fpp, avg_return %+0.2fpp | grades: %s | A=%d",
            indicator,
            pooled.get(20, {}).get("win_rate_pct", 0) - baseline_pooled.get(20, {}).get("win_rate_pct", 0),
            pooled.get(20, {}).get("avg_return_pct", 0) - baseline_pooled.get(20, {}).get("avg_return_pct", 0),
            {g: sum(1 for x in grades.values() if x["grade"] == g) for g in ("A", "B", "C", "D", "N/A")},
            a_count,
        )


if __name__ == "__main__":
    main()
