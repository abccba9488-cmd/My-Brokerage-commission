"""Broker concentration index (CSI): top-N net-buy brokers as a share of total volume."""
from __future__ import annotations

import pandas as pd


def compute_csi(broker_df: pd.DataFrame, total_volume: int, top_n: int) -> float:
    """0~100. Sums net buy of the top-N net-buying brokers over the window,
    divided by total traded volume over the same window."""
    if broker_df.empty or total_volume == 0:
        return 0.0
    by_broker = broker_df.groupby("broker_id").apply(
        lambda g: (g["buy_shares"] - g["sell_shares"]).sum(), include_groups=False
    )
    top_net = by_broker.sort_values(ascending=False).head(top_n)
    top_net = top_net[top_net > 0]  # only count net buyers toward concentration
    csi = top_net.sum() / total_volume * 100
    return round(min(max(csi, 0.0), 100.0), 1)
