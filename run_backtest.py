"""Backtest the broker buy-streak signal over real historical data.

Usage:
    python run_backtest.py [--days N]

Fetches ~N calendar days of price + broker branch data per stock in
config/stocks.yaml (incrementally cached, like run_daily.py), rolls the
buy-streak signal across every historical day, and reports forward-return
win rate / avg return / max drawdown per holding period — both pooled
across the whole watchlist and broken out per stock.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from src.backtest import backtest, broker_signal, credibility
from src.indicators.broker_streak import filter_by_volume_share
from src.ingest import fetch_broker, fetch_price
from src.ingest.finmind_client import has_sponsor_token
from src.storage import db

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config" / "stocks.yaml"
CREDIBILITY_PATH = Path(__file__).parent / "config" / "signal_credibility.yaml"
REPORTS_DIR = Path(__file__).parent / "reports"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def fetch_and_cache_history(sid: str, start_date: str, end_date: str, conn) -> tuple[pd.DataFrame, pd.DataFrame]:
    price_rows = fetch_price.fetch(sid, start_date, end_date)
    if not price_rows:
        return pd.DataFrame(), pd.DataFrame()
    db.upsert_rows(conn, "stock_price", price_rows)
    price_df = pd.DataFrame(price_rows).sort_values("date").reset_index(drop=True)

    already_have = db.get_existing_dates(conn, "broker_trade", sid)
    missing_dates = [d for d in price_df["date"] if d not in already_have]
    new_rows: list[dict] = []
    for i, trade_date in enumerate(missing_dates):
        new_rows.extend(fetch_broker.fetch(sid, trade_date))
        # Throttle: observed per-request latency alone (~0.55s) already sits right
        # at FinMind's 6000/hour Sponsor ceiling, and a 38-stock backfill actually
        # hit the limit at 0.15s padding (partly from run_daily.py using the same
        # quota concurrently — don't run both at once). More margin here.
        time.sleep(0.3)
        if (i + 1) % 20 == 0:
            db.upsert_rows(conn, "broker_trade", new_rows)
            new_rows = []
            logger.info("%s: fetched %d/%d broker dates", sid, i + 1, len(missing_dates))
    if new_rows:
        db.upsert_rows(conn, "broker_trade", new_rows)
    logger.info("%s: %d new broker date(s) fetched, %d already cached", sid, len(missing_dates), len(already_have))

    placeholders = ",".join("?" for _ in price_df["date"])
    cur = conn.execute(
        f"SELECT stock_id, date, broker_id, broker_name, buy_shares, sell_shares, price "
        f"FROM broker_trade WHERE stock_id = ? AND date IN ({placeholders})",
        [sid, *price_df["date"]],
    )
    broker_df = pd.DataFrame(
        cur.fetchall(),
        columns=["stock_id", "date", "broker_id", "broker_name", "buy_shares", "sell_shares", "price"],
    )
    return price_df, broker_df


def _table_rows(results: dict) -> list[str]:
    rows = []
    for h, r in results.items():
        if r.get("sample_count", 0) == 0:
            rows.append(f"| {h} | 0 | - | - | - | - |")
        else:
            rows.append(
                f"| {h} | {r['sample_count']} | {r['win_rate_pct']}% | "
                f"{r['avg_return_pct']:+.2f}% | {r['median_return_pct']:+.2f}% | {r['max_drawdown_pct']:.2f}% |"
            )
    return rows


def _edge_rows(signal: dict, baseline: dict) -> list[str]:
    """Signal stats minus the 'enter on any random day' baseline — this is
    what actually tells you whether the signal beats just holding the stock,
    versus merely riding the same period's general drift."""
    rows = []
    for h in signal:
        s, b = signal[h], baseline.get(h, {})
        if s.get("sample_count", 0) == 0 or b.get("sample_count", 0) == 0:
            rows.append(f"| {h} | - | - |")
            continue
        win_edge = s["win_rate_pct"] - b["win_rate_pct"]
        ret_edge = s["avg_return_pct"] - b["avg_return_pct"]
        rows.append(f"| {h} | {win_edge:+.1f}pp | {ret_edge:+.2f}pp |")
    return rows


def render_report(
    per_stock: dict,
    pooled: dict,
    baseline_per_stock: dict,
    baseline_pooled: dict,
    grades: dict,
    run_date: str,
    days: int,
) -> str:
    lines = [
        f"# 分點連續買超訊號回測報告 — {run_date}",
        "",
        f"訊號定義：任一券商分點連續買超 >= 門檻天數（見 config/stocks.yaml `broker.streak_min_days`），"
        f"回測範圍約 {days} 個日曆天。",
        "",
        "**基準線**：同一檔股票、同一段期間，如果不看訊號、每個交易日都進場，平均會是什麼結果。"
        "訊號要贏過基準線，才算是真的有鑑別度，不然只是搭上這段期間股價本身的漲勢。",
        "",
        "## 全部股票合併結果",
        "",
        "### 訊號進場",
        "| 持有天數 | 樣本數 | 勝率 | 平均報酬 | 中位數報酬 | 最大回撤 |",
        "|---|---|---|---|---|---|",
        *_table_rows(pooled),
        "",
        "### 基準線（每日進場）",
        "| 持有天數 | 樣本數 | 勝率 | 平均報酬 | 中位數報酬 | 最大回撤 |",
        "|---|---|---|---|---|---|",
        *_table_rows(baseline_pooled),
        "",
        "### 訊號相對基準線的優勢（正值＝訊號比亂買好）",
        "| 持有天數 | 勝率差 | 平均報酬差 |",
        "|---|---|---|",
        *_edge_rows(pooled, baseline_pooled),
        "",
        "## 訊號可信度分級（依 10日／20日持有期的優勢與交易成本估算評定）",
        "",
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
        lines.append("")
        lines.append(f"{g['reason']}")
        lines.append("")
        lines.append("訊號進場：")
        lines.append("| 持有天數 | 樣本數 | 勝率 | 平均報酬 | 中位數報酬 | 最大回撤 |")
        lines.append("|---|---|---|---|---|---|")
        lines += _table_rows(results)
        lines.append("")
        lines.append("基準線（每日進場）：")
        lines.append("| 持有天數 | 樣本數 | 勝率 | 平均報酬 | 中位數報酬 | 最大回撤 |")
        lines.append("|---|---|---|---|---|---|")
        lines += _table_rows(baseline)
        lines.append("")
        lines.append("訊號相對基準線的優勢：")
        lines.append("| 持有天數 | 勝率差 | 平均報酬差 |")
        lines.append("|---|---|---|")
        lines += _edge_rows(results, baseline)
        lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=365, help="calendar days of history to backtest over")
    args = parser.parse_args()

    if not has_sponsor_token():
        logger.error("FINMIND_TOKEN not set — broker-branch backtest requires FinMind Sponsor.")
        sys.exit(1)

    config = load_config()
    db.init_db()
    conn = db.get_connection()

    end_date = datetime.today()
    start_date = end_date - timedelta(days=args.days)
    end_str, start_str = end_date.strftime("%Y-%m-%d"), start_date.strftime("%Y-%m-%d")

    broker_cfg = config["broker"]
    lookback = config["lookback"]["long"]
    holding_days_list = config["backtest"]["holding_days"]

    stock_signal_pairs = []
    stock_baseline_pairs = []
    per_stock_results = {}
    per_stock_baseline = {}

    for stock in config["stocks"]:
        sid = stock["code"]
        try:
            price_df, broker_df = fetch_and_cache_history(sid, start_str, end_str, conn)
        except Exception as exc:
            # A per-stock crash used to take down the whole batch, discarding
            # everything already fetched for earlier stocks (see run
            # 2026-07-03 11:xx: 13 stocks and ~40min of history backfill lost
            # to a single rate-limit error on stock #14). Skip this stock and
            # keep going instead — incremental caching means the next run
            # only needs to re-fetch what's still missing.
            logger.error("%s: fetch failed, skipping this run — %s", sid, exc)
            if "upper limit" in str(exc).lower():
                logger.error(
                    "FinMind rate limit reached — stopping early. Re-run later; "
                    "already-cached stocks/dates won't be re-fetched."
                )
                break
            continue

        if price_df.empty:
            logger.warning("No price data for %s, skipping", sid)
            continue

        broker_df = filter_by_volume_share(broker_df, price_df, broker_cfg["volume_share_min_pct"])
        signals = broker_signal.signal_dates(
            broker_df,
            streak_min_days=broker_cfg["streak_min_days"],
            allow_gap_days=broker_cfg["streak_allow_gap_days"],
            lookback_days=lookback,
        )
        logger.info("%s: %d buy-streak signal dates found", sid, len(signals))

        all_dates = set(price_df["date"])

        stock_signal_pairs.append((price_df, signals))
        stock_baseline_pairs.append((price_df, all_dates))
        per_stock_results[sid] = backtest.run(price_df, signals, holding_days_list)
        per_stock_baseline[sid] = backtest.run(price_df, all_dates, holding_days_list)

    conn.close()

    if not per_stock_results:
        logger.error("No stocks produced results — nothing to report.")
        sys.exit(1)

    pooled = backtest.run_multi(stock_signal_pairs, holding_days_list)
    baseline_pooled = backtest.run_multi(stock_baseline_pairs, holding_days_list)

    run_date = end_date.strftime("%Y-%m-%d")

    grades = {}
    for sid, results in per_stock_results.items():
        baseline = per_stock_baseline[sid]
        if 10 in results and 20 in results:
            grades[sid] = credibility.grade(results[10], baseline[10], results[20], baseline[20])
        else:
            grades[sid] = {"grade": "N/A", "reason": "缺少10日或20日持有期資料"}

    credibility_out = {
        "generated_from_backtest_date": run_date,
        "backtest_window_days": args.days,
        "stocks": {sid: g for sid, g in grades.items()},
    }
    CREDIBILITY_PATH.write_text(
        yaml.safe_dump(credibility_out, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    logger.info("Signal credibility grades written to %s", CREDIBILITY_PATH)

    report = render_report(per_stock_results, pooled, per_stock_baseline, baseline_pooled, grades, run_date, args.days)
    out_path = REPORTS_DIR / f"backtest_{run_date}.md"
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
