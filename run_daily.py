"""Main entry point: fetch data, compute indicators, write the daily report.

Usage:
    python run_daily.py

Reads config/stocks.yaml for the watchlist and thresholds. Broker branch
(分點) data is only fetched if FINMIND_TOKEN is set in .env (FinMind
Sponsor); otherwise those fields degrade gracefully and the report says so.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from src.ingest import fetch_broker, fetch_institutional, fetch_lending, fetch_margin, fetch_price
from src.ingest.finmind_client import has_sponsor_token
from src.indicators import (
    accumulation_score,
    broker_cost,
    broker_streak,
    chip_health,
    concentration,
    entry_exit_signal,
    margin_risk,
    signal_light,
    volume_price,
)
from src.report import render
from src.storage import db

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config" / "stocks.yaml"


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def analyze_stock(stock: dict, start_date: str, end_date: str, config: dict, conn) -> dict | None:
    sid = stock["code"]

    price_rows = fetch_price.fetch(sid, start_date, end_date)
    if not price_rows:
        logger.warning("No price data for %s, skipping", sid)
        return None
    inst_rows = fetch_institutional.fetch(sid, start_date, end_date)
    margin_rows = fetch_margin.fetch(sid, start_date, end_date)
    lending_rows = fetch_lending.fetch(sid, start_date, end_date)

    db.upsert_rows(conn, "stock_price", price_rows)
    db.upsert_rows(conn, "institutional", inst_rows)
    db.upsert_rows(conn, "margin", margin_rows)
    db.upsert_rows(conn, "lending", lending_rows)

    price_df = pd.DataFrame(price_rows).sort_values("date").reset_index(drop=True)
    inst_df = pd.DataFrame(inst_rows)
    margin_df = pd.DataFrame(margin_rows) if margin_rows else pd.DataFrame(columns=["date", "margin_buy", "margin_balance"])
    lending_df = pd.DataFrame(lending_rows)

    broker_available = has_sponsor_token()
    broker_rows: list[dict] = []
    if broker_available:
        for trade_date in price_df["date"]:
            broker_rows.extend(fetch_broker.fetch(sid, trade_date))
        db.upsert_rows(conn, "broker_trade", broker_rows)
    else:
        logger.info("Skipping broker branch data for %s (no FinMind Sponsor token)", sid)
    broker_df = pd.DataFrame(broker_rows) if broker_rows else pd.DataFrame(
        columns=["stock_id", "date", "broker_id", "broker_name", "buy_shares", "sell_shares", "price"]
    )

    lookback = config["lookback"]["long"]
    broker_cfg = config["broker"]

    # price_df/margin_df/inst_df are fetched with an extra buffer for rolling
    # windows to warm up (see main()), but broker indicators (streak/cost/CSI)
    # must all agree on the same recent window, otherwise CSI's numerator
    # covers more days than its volume denominator, and cost estimates pick up
    # stale pre-streak trades. Window broker_df down to the same `lookback`
    # trading days used everywhere else.
    recent_dates = set(price_df["date"].tail(lookback))
    broker_df = broker_df[broker_df["date"].isin(recent_dates)]

    mr = margin_risk.latest(price_df, margin_df, lookback, config)
    vp = volume_price.latest(price_df, lookback)

    latest_close = float(price_df["close"].iloc[-1])
    # FinMind returns institutional buy/sell in shares; convert to 張 (board lots, 1張=1000股) for display.
    latest_foreign_net = int(inst_df.sort_values("date")["foreign_net"].iloc[-1] / 1000) if not inst_df.empty else 0
    latest_trust_net = int(inst_df.sort_values("date")["trust_net"].iloc[-1] / 1000) if not inst_df.empty else 0

    streaks = broker_streak.compute(broker_df, broker_cfg["streak_min_days"], broker_cfg["streak_allow_gap_days"])
    top_streak = streaks.iloc[0] if not streaks.empty else None
    period_volume = int(price_df["volume"].tail(lookback).sum())

    broker_sell_streak_days = 0
    broker_buy_streak_days = 0
    csi = 0.0
    if top_streak is not None and top_streak["direction"] == "buy":
        cost = broker_cost.estimate_cost(broker_df, broker_ids=[top_streak["broker_id"]])
        pnl = broker_cost.profit_status(cost, latest_close)
        csi = concentration.compute_csi(broker_df, period_volume, broker_cfg["top_n_concentration"])
        acc = accumulation_score.compute(
            streak_days=int(top_streak["streak_days"]),
            net_buy_volume=int(top_streak["total_net"]),
            period_volume=period_volume,
            csi=csi,
            cost_trend=top_streak["trend"],
            pnl_pct=pnl["pnl_pct"],
            foreign_net=latest_foreign_net,
            trust_net=latest_trust_net,
        )
        broker_buy_streak_days = int(top_streak["streak_days"])
    else:
        pnl = {"cost": None, "pnl_pct": None, "status": "unknown"}
        acc = {"score": 0.0, "components": {}}
        if top_streak is not None and top_streak["direction"] == "sell":
            broker_sell_streak_days = int(top_streak["streak_days"])

    lending_trend = "flat"
    if len(lending_df) >= 2:
        ld = lending_df.sort_values("date")
        if ld["lending_balance"].iloc[-1] > ld["lending_balance"].iloc[0]:
            lending_trend = "increasing"
        elif ld["lending_balance"].iloc[-1] < ld["lending_balance"].iloc[0]:
            lending_trend = "decreasing"

    health = chip_health.compute(
        accumulation_score=acc["score"],
        foreign_net=latest_foreign_net,
        trust_net=latest_trust_net,
        margin_risk_level=mr.get("risk_level", "warning"),
        lending_balance_trend=lending_trend,
    )

    light = signal_light.bull_bear_light(health["score"], config)
    alert = signal_light.sell_off_alert(
        broker_sell_streak_days=broker_sell_streak_days,
        pnl_pct=pnl["pnl_pct"],
        vp_pattern=vp.get("vp_pattern", "unknown"),
        margin_risk_level=mr.get("risk_level", "warning"),
        false_breakout_risk=vp.get("false_breakout_risk", False),
        config=config,
    )

    entry_signal = entry_exit_signal.evaluate_entry(
        broker_available=broker_available,
        broker_streak_days=broker_buy_streak_days,
        broker_direction="buy" if broker_buy_streak_days > 0 else "none",
        csi=csi,
        foreign_net=latest_foreign_net,
        trust_net=latest_trust_net,
        pnl_pct=pnl["pnl_pct"],
        volume=vp.get("volume", 0),
        avg_volume=vp.get("avg_volume", 0),
        config=config,
    )
    exit_signal = entry_exit_signal.evaluate_exit(
        broker_available=broker_available,
        pnl_pct=pnl["pnl_pct"],
        close=latest_close,
        ma_long=vp.get("ma_long", latest_close),
        broker_sell_streak_days=broker_sell_streak_days,
        sell_alert_triggered=alert["alert"],
        config=config,
    )

    return {
        "stock_id": sid,
        "name": stock["name"],
        "close": latest_close,
        "light": light,
        "chip_health": health,
        "accumulation_score": acc,
        "margin_risk": mr,
        "volume_price": vp,
        "foreign_net": latest_foreign_net,
        "trust_net": latest_trust_net,
        "broker_available": broker_available,
        "broker_cost": pnl,
        "sell_alert": alert,
        "entry_signal": entry_signal,
        "exit_signal": exit_signal,
    }


def main() -> None:
    config = load_config()
    db.init_db()
    conn = db.get_connection()

    end_date = datetime.today()
    start_date = end_date - timedelta(days=config["lookback"]["long"] * 3)  # buffer for rolling windows
    end_str, start_str = end_date.strftime("%Y-%m-%d"), start_date.strftime("%Y-%m-%d")

    results = []
    for stock in config["stocks"]:
        try:
            r = analyze_stock(stock, start_str, end_str, config, conn)
            if r:
                results.append(r)
        except Exception:
            logger.exception("Failed to analyze %s", stock["code"])

    conn.close()

    if not results:
        logger.error("No results produced — check FinMind connectivity or date range.")
        sys.exit(1)

    paths = render.save_report(results, end_date.strftime("%Y-%m-%d"))
    print(f"Report saved: {paths['markdown']}")
    print(f"CSV saved: {paths['csv']}")


if __name__ == "__main__":
    main()
