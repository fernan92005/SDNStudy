"""Aggregate per-tick / per-link metrics into scalar summaries per experiment."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict

import numpy as np
import pandas as pd


@dataclass
class ExperimentSummary:
    scenario: str
    policy: str
    ticks: int
    avg_latency_ms: float
    p95_latency_ms: float
    avg_throughput_mbps: float
    total_throughput_gbits: float
    avg_packet_loss_pct: float
    congestion_event_ticks: int
    congestion_event_duration_pct: float
    avg_utilization: float
    max_utilization: float


def summarize_experiment(
    df: pd.DataFrame,
    congestion_threshold: float = 0.9,
) -> ExperimentSummary:
    """Aggregate one experiment's per-tick / per-link DataFrame.

    Latency and loss are averaged over the *active path edges* only so they
    reflect what the source-destination flow actually experienced, not the
    idle links elsewhere in the topology.
    """
    if df.empty:
        raise ValueError("Cannot summarize empty DataFrame")

    scenario = str(df["scenario"].iloc[0])
    policy = str(df["policy"].iloc[0])

    if "is_on_active_path" in df.columns:
        path_rows = df[df["is_on_active_path"].astype(bool)]
    else:
        path_rows = df[df["offered_load_mbps"] > 0.0]
    if path_rows.empty:
        path_rows = df

    per_tick = path_rows.groupby("tick").agg(
        latency_ms=("latency_ms", "sum"),
        packet_loss_pct=("packet_loss_pct", "max"),
        utilization=("utilization", "max"),
        throughput_mbps=("carried_load_mbps", "min"),
    )

    # For multi-path policies (e.g. ECMP), the simulator pre-computes per-tick
    # flow-level metrics that account for the weighted split across paths:
    # naively reducing across active edges would double-count latency,
    # under-report flow throughput (each path's bottleneck), and over-report
    # loss (worst link on any path). For single-path policies (weight = 1.0)
    # the "effective_*" values reduce to the natural per-path values, so
    # static and predictive numbers are unchanged.
    per_tick = per_tick.copy()
    if "effective_latency_ms" in df.columns:
        eff_lat = df.groupby("tick")["effective_latency_ms"].first()
        if not eff_lat.empty and float(eff_lat.max()) > 0:
            per_tick["latency_ms"] = eff_lat.reindex(per_tick.index).fillna(
                per_tick["latency_ms"]
            )
    if "effective_throughput_mbps" in df.columns:
        eff_tp = df.groupby("tick")["effective_throughput_mbps"].first()
        if not eff_tp.empty and float(eff_tp.max()) > 0:
            per_tick["throughput_mbps"] = eff_tp.reindex(per_tick.index).fillna(
                per_tick["throughput_mbps"]
            )
    if "effective_packet_loss_pct" in df.columns:
        eff_loss = df.groupby("tick")["effective_packet_loss_pct"].first()
        if eff_loss.notna().any():
            per_tick["packet_loss_pct"] = eff_loss.reindex(per_tick.index).fillna(
                per_tick["packet_loss_pct"]
            )

    ticks = int(per_tick.shape[0])
    avg_latency = float(per_tick["latency_ms"].mean())
    p95_latency = float(per_tick["latency_ms"].quantile(0.95))
    avg_loss = float(per_tick["packet_loss_pct"].mean())
    avg_util = float(per_tick["utilization"].mean())
    max_util = float(per_tick["utilization"].max())
    avg_throughput = float(per_tick["throughput_mbps"].mean())
    total_gbits = float(per_tick["throughput_mbps"].sum() / 1000.0)

    congested = per_tick["utilization"] >= congestion_threshold
    congestion_ticks = int(congested.sum())
    congestion_pct = float(congestion_ticks / max(1, ticks) * 100.0)

    return ExperimentSummary(
        scenario=scenario,
        policy=policy,
        ticks=ticks,
        avg_latency_ms=avg_latency,
        p95_latency_ms=p95_latency,
        avg_throughput_mbps=avg_throughput,
        total_throughput_gbits=total_gbits,
        avg_packet_loss_pct=avg_loss,
        congestion_event_ticks=congestion_ticks,
        congestion_event_duration_pct=congestion_pct,
        avg_utilization=avg_util,
        max_utilization=max_util,
    )


def summary_to_dict(s: ExperimentSummary) -> Dict[str, float | str | int]:
    return asdict(s)
