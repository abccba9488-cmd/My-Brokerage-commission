"""Out-of-sample robustness check for the 2 stocks that passed FDR in the
composite (>=4/5 condition) backtest: 2491 and 6175 (see
系統功能說明與測試結果.md 6.7).

The composite signal is a FIXED rule (thresholds from config/stocks.yaml,
nothing fitted on data), so there's no train/test split in the usual
walk-forward sense. The relevant question instead is split-sample
stability: does the edge show up consistently across independent
chronological sub-periods of the 3-year window, or is it concentrated in
one lucky stretch (which is what you'd expect from a false positive that
only survived FDR because of a handful of unusually good trades)?

Splits the available history into 3 non-overlapping terciles and reruns
the same composite-signal backtest independently within each. Cache-only,
no FinMind calls.

Usage:
    python run_walkforward_candidates.py
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd
import yaml

from run_backtest_composite import load_from_cache
from src.backtest import backtest, composite_signal, significance
from src.storage import db

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).parent / "config" / "stocks.yaml"
CANDIDATES = ["2491", "6175"]
N_TERCILES = 3


def load_config() -> dict:
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))


def main() -> None:
    config = load_config()
    lookback = config["lookback"]["long"]
    holding_days_list = config["backtest"]["holding_days"]

    conn = db.get_connection()
    lines = ["# 2491／6175 分段穩健性檢查（3年資料切3段，各自獨立回測）", ""]

    for sid in CANDIDATES:
        price_df, inst_df, broker_df = load_from_cache(conn, sid, "2000-01-01", "2100-01-01")
        price_df = price_df.sort_values("date").reset_index(drop=True)
        n = len(price_df)
        tercile_size = n // N_TERCILES

        lines.append(f"## {sid}")
        lines.append("")
        lines.append(f"全期間：{price_df['date'].iloc[0]} ~ {price_df['date'].iloc[-1]}（{n}個交易日）")
        lines.append("")
        lines.append("| 分段 | 期間 | 訊號數 | 10日勝率 | 10日優勢 | 20日勝率 | 20日優勢 | p值(20日) |")
        lines.append("|---|---|---|---|---|---|---|---|")

        for i in range(N_TERCILES):
            start_i = i * tercile_size
            end_i = n if i == N_TERCILES - 1 else (i + 1) * tercile_size
            seg_price = price_df.iloc[start_i:end_i].reset_index(drop=True)
            seg_dates = set(seg_price["date"])
            seg_inst = inst_df[inst_df["date"].isin(seg_dates)].reset_index(drop=True)
            seg_broker = broker_df[broker_df["date"].isin(seg_dates)].reset_index(drop=True)

            signals = composite_signal.signal_dates(seg_price, seg_inst, seg_broker, lookback, config)
            all_dates = set(seg_price["date"])

            results = backtest.run(seg_price, signals, holding_days_list)
            baseline = backtest.run(seg_price, all_dates, holding_days_list)

            def cell(h):
                s, b = results.get(h, {}), baseline.get(h, {})
                if s.get("sample_count", 0) == 0:
                    return "0", "-"
                edge = f"{s['win_rate_pct']-b.get('win_rate_pct',0):+.1f}pp"
                return f"{s['sample_count']} ({s['win_rate_pct']}%)", edge

            n10, edge10 = cell(10)
            n20, edge20 = cell(20)

            p_val = "-"
            sig_trades = backtest.trades_for_holding(seg_price, signals, 20)
            base_trades = backtest.trades_for_holding(seg_price, all_dates, 20)
            if len(sig_trades) >= 5 and len(base_trades) >= 5:
                mw = significance.mann_whitney_test(
                    [t["return_pct"] for t in sig_trades], [t["return_pct"] for t in base_trades]
                )
                p_val = f"{mw['p_value']:.3f}"

            period = f"{seg_price['date'].iloc[0]} ~ {seg_price['date'].iloc[-1]}"
            n_sig = len(signals)
            lines.append(f"| {i+1} | {period} | {n_sig} | {n10} | {edge10} | {n20} | {edge20} | {p_val} |")
            logger.info("%s segment %d/%d: %d signals, period %s", sid, i + 1, N_TERCILES, n_sig, period)

        lines.append("")

    conn.close()

    out_path = Path(__file__).parent / "reports" / "walkforward_candidates_2491_6175.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nSaved: {out_path}")


if __name__ == "__main__":
    main()
