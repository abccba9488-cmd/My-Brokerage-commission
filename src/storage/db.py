"""SQLite schema and access layer for the chip-flow analysis system."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "chips.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS stock_price (
    stock_id TEXT NOT NULL,
    date TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume INTEGER,
    PRIMARY KEY (stock_id, date)
);

CREATE TABLE IF NOT EXISTS institutional (
    stock_id TEXT NOT NULL,
    date TEXT NOT NULL,
    foreign_net INTEGER,
    trust_net INTEGER,
    dealer_net INTEGER,
    PRIMARY KEY (stock_id, date)
);

CREATE TABLE IF NOT EXISTS margin (
    stock_id TEXT NOT NULL,
    date TEXT NOT NULL,
    margin_buy INTEGER, margin_sell INTEGER, margin_balance INTEGER, margin_limit INTEGER,
    short_buy INTEGER, short_sell INTEGER, short_balance INTEGER,
    PRIMARY KEY (stock_id, date)
);

CREATE TABLE IF NOT EXISTS lending (
    stock_id TEXT NOT NULL,
    date TEXT NOT NULL,
    lending_balance INTEGER,
    lending_sell INTEGER,
    PRIMARY KEY (stock_id, date)
);

-- Broker branch (分點) daily detail. Requires FinMind Sponsor; empty until token is set.
CREATE TABLE IF NOT EXISTS broker_trade (
    stock_id TEXT NOT NULL,
    date TEXT NOT NULL,
    broker_id TEXT NOT NULL,
    broker_name TEXT,
    buy_shares INTEGER,
    sell_shares INTEGER,
    price REAL,
    PRIMARY KEY (stock_id, date, broker_id, price)
);

-- Alias table: same brokerage branch can appear under renamed/merged codes over time.
CREATE TABLE IF NOT EXISTS broker_alias (
    broker_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL
);

-- Shareholder concentration (股東持股分級表), weekly. Pre-aggregated at ingest
-- time into major (>=400,001 shares, i.e. 400張+) vs retail share of ownership,
-- rather than storing all ~15 raw tiers — that's the only cut the indicators use.
CREATE TABLE IF NOT EXISTS holder_concentration (
    stock_id TEXT NOT NULL,
    date TEXT NOT NULL,
    major_holder_pct REAL,
    retail_holder_pct REAL,
    major_holder_people INTEGER,
    total_people INTEGER,
    PRIMARY KEY (stock_id, date)
);

-- Foreign ownership ratio (外資持股比例), daily. Distinct from `institutional`
-- (today's buy/sell net) — this tracks the cumulative ownership level/trend.
CREATE TABLE IF NOT EXISTS foreign_shareholding (
    stock_id TEXT NOT NULL,
    date TEXT NOT NULL,
    foreign_shares_ratio REAL,
    foreign_remain_ratio REAL,
    PRIMARY KEY (stock_id, date)
);

-- 八大行庫 (government-linked bank) net buy/sell, daily, aggregated across all 8 banks.
CREATE TABLE IF NOT EXISTS government_bank (
    stock_id TEXT NOT NULL,
    date TEXT NOT NULL,
    net_shares INTEGER,
    PRIMARY KEY (stock_id, date)
);
"""


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db() -> None:
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()


def upsert_rows(conn: sqlite3.Connection, table: str, rows: list[dict]) -> None:
    """Insert-or-replace a list of dict rows into `table`. Columns come from row keys."""
    if not rows:
        return
    columns = list(rows[0].keys())
    placeholders = ", ".join(f":{c}" for c in columns)
    col_list = ", ".join(columns)
    sql = f"INSERT OR REPLACE INTO {table} ({col_list}) VALUES ({placeholders})"
    conn.executemany(sql, rows)
    conn.commit()


def get_existing_dates(conn: sqlite3.Connection, table: str, stock_id: str) -> set[str]:
    """Dates already stored for this stock in `table`. A finalized trading
    day's data doesn't change, so callers can use this to skip re-fetching
    days that are already in the database (see broker_trade, which is
    expensive to re-pull: one API call and thousands of rows per day)."""
    cur = conn.execute(f"SELECT DISTINCT date FROM {table} WHERE stock_id = ?", (stock_id,))
    return {row[0] for row in cur.fetchall()}


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
