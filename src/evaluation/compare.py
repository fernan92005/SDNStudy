"""Compare routing policies under each scenario.

The static shortest-path router is always the baseline. Every other policy
(predictive, ECMP, ...) is reported as a percent delta vs that baseline,
so the summary file is policy-agnostic and ready for N-way comparisons.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

import pandas as pd

from src.evaluation.metrics import ExperimentSummary, summary_to_dict


METRIC_COLUMNS = (
    "avg_latency_ms",
    "p95_latency_ms",
    "avg_throughput_mbps",
    "avg_packet_loss_pct",
    "congestion_event_duration_pct",
    "avg_utilization",
    "max_utilization",
)


def build_summary_table(summaries: List[ExperimentSummary]) -> pd.DataFrame:
    return pd.DataFrame([summary_to_dict(s) for s in summaries])


def compute_deltas(table: pd.DataFrame, baseline: str = "static") -> pd.DataFrame:
    """For each scenario, compute (policy - baseline) / baseline as percent
    for every non-baseline policy present in the table.
    """
    rows: List[Dict[str, float | str]] = []
    for scenario, group in table.groupby("scenario"):
        policies = set(group["policy"].unique())
        if baseline not in policies:
            continue
        base_row = group[group["policy"] == baseline].iloc[0]
        for policy in sorted(p for p in policies if p != baseline):
            comp = group[group["policy"] == policy].iloc[0]
            entry: Dict[str, float | str] = {
                "scenario": scenario,
                "baseline": baseline,
                "policy": policy,
            }
            for col in METRIC_COLUMNS:
                base = float(base_row[col])
                pv = float(comp[col])
                entry[f"{col}_baseline"] = base
                entry[f"{col}_policy"] = pv
                entry[f"{col}_delta_pct"] = (
                    (pv - base) / abs(base) * 100.0 if base != 0.0 else 0.0
                )
            rows.append(entry)
    return pd.DataFrame(rows)


def write_csv(table: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_csv(path, index=False)
    return path
