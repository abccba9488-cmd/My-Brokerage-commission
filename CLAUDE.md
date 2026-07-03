# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

台股主力分點＋籌碼流向分析系統（Phase 1 MVP）：規則式指標計算 + 每日 Markdown/CSV 報表 + 回測框架，用來驗證訊號是否真的有效。所有分數/燈號都是規則式假設，未經回測驗證前不構成投資建議（詳見 README.md「已知限制」）。

## Commands

```bash
# Setup (always use the project venv — installing FinMind globally downgrades
# the system pandas and can break other Python projects on this machine)
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env

# Run the full daily pipeline (writes reports/YYYY-MM-DD.md and .csv)
.venv\Scripts\python run_daily.py

# Re-initialize the SQLite schema (idempotent, CREATE TABLE IF NOT EXISTS)
.venv\Scripts\python src\storage\db.py
```

There is no formal test suite. Modules are verified by running them directly against real FinMind data or small fixture dicts (see commit history for examples per module). When testing at the shell, always set `PYTHONIOENCODING=utf-8` — output strings are zh-TW and the default Windows console codepage mangles them (data itself is unaffected, it's a display-only issue).

分點（券商分點）功能需要 FinMind Sponsor token（`FINMIND_TOKEN` in `.env`）才能實測；沒有 token 時，這部分程式碼路徑只能靠空 DataFrame / fixture 驗證。

## Architecture / data flow

`run_daily.py` is the sole orchestrator. `main()` loops `config/stocks.yaml`'s watchlist and calls `analyze_stock()` once per ticker. Pipeline per stock:

1. **`src/ingest/fetch_*.py`** — thin wrappers around the FinMind SDK, each exposing `fetch(...) -> list[dict]`. Free tier: `fetch_price`, `fetch_institutional`, `fetch_margin`, `fetch_lending`. Sponsor-only: `fetch_broker` (分點), whose FinMind endpoint is single-day-per-request, so it's called once per trading date in the window.
2. **`src/storage/db.py`** — SQLite at `data/chips.db`. `db.upsert_rows()` does `INSERT OR REPLACE` keyed by each table's primary key, so re-running `run_daily.py` is idempotent.
3. **`src/indicators/*.py`** — pure functions over pandas DataFrames, no I/O. Each module exposes a `compute(...)` and/or `latest(...)` entry point returning a plain dict.
4. **`src/report/render.py`** — assembles the list of per-stock result dicts into Markdown + CSV.
5. **`src/backtest/backtest.py`** — standalone, *not* wired into `run_daily.py`. Turn any indicator into a `set[str]` of signal dates and run `backtest.run()` to get forward-return win rate / avg return / max drawdown. Use this before trusting any new rule-based indicator.

## Key design constraint: graceful degradation without FinMind Sponsor

`FINMIND_TOKEN` gates all 分點 (broker branch) functionality via `has_sponsor_token()` in `src/ingest/finmind_client.py`. Without it:

- `fetch_broker.fetch()` returns `[]` and logs a warning — it never raises.
- Every broker-derived indicator (`broker_streak`, `broker_cost`, `concentration`, the broker-weighted terms in `accumulation_score`, the broker-gated conditions in `entry_exit_signal`) must produce a defined, non-crashing result on an empty `broker_df`. Preserve this pattern when adding new broker-dependent indicators.
- `render.py` must show "未啟用（需訂閱）" instead of a fabricated number for anything broker-derived.
- A broker-gated condition must never silently count as "met" when data is unavailable — e.g. `entry_exit_signal.py` tracks `conditions_unavailable` separately so a BUY signal can never fire on incomplete data.

## Config-driven thresholds

All tunable thresholds (streak days, CSI top-N, margin maintenance %, entry/exit condition counts, backtest holding periods) live in `config/stocks.yaml`, not hardcoded in indicator modules. New thresholds should follow the same pattern rather than being inlined.

## Window consistency

Every "recent window" calculation must use `config["lookback"]["long"]` (default 20 trading days) consistently. `run_daily.py` fetches a wider buffer (`lookback * 3` calendar days) so rolling-window indicators (moving averages, avg volume) have history to warm up — but before broker-based indicators run, `broker_df` is filtered back down to exactly the last `lookback` trading days (`recent_dates = set(price_df["date"].tail(lookback))`). This was a real bug: mismatched windows inflated the concentration index and let stale pre-streak trades skew cost estimates (fixed in commit `b6801eb`). Keep numerator/denominator windows aligned when adding new ratio-style indicators.

## Units

FinMind returns share counts (股), not board lots (張, 1張=1000股). Institutional net buy/sell is converted to 張 only at the display layer in `run_daily.py` (`/1000`) — keep raw values in shares everywhere else (DB, indicator math) to avoid unit-mismatch bugs.

## FinMind loader

`src/ingest/finmind_client.get_loader()` caches a single authenticated `DataLoader` at module scope — don't re-instantiate `DataLoader()` per call, especially inside `fetch_broker.py`'s per-date loop, which previously re-authenticated on every single-day request.
