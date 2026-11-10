"""Stage 5: aggregate metrics, compute deltas, and render comparison plots."""
from __future__ import annotations

import _bootstrap  # noqa: F401

from pathlib import Path
from typing import Dict, List

import pandas as pd

from src.config import DATA_DIR, FIGURES_DIR, RESULTS_DIR, ensure_dirs
from src.evaluation.compare import build_summary_table, compute_deltas, write_csv
from src.evaluation.metrics import summarize_experiment
from src.visualization.plots import (
    plot_latency_timeseries,
    plot_metric_bars,
    plot_throughput_timeseries,
    plot_utilization_timeseries,
)


SCENARIOS = ("normal", "congestion")
REQUIRED_POLICIES = ("static", "predictive")
OPTIONAL_POLICIES = ("ecmp",)


def _load_runs() -> Dict[str, pd.DataFrame]:
    runs: Dict[str, pd.DataFrame] = {}
    for scen in SCENARIOS:
        for policy in REQUIRED_POLICIES:
            key = f"{scen}_{policy}"
            csv_path = DATA_DIR / f"{key}.csv"
            if not csv_path.exists():
                raise SystemExit(
                    f"Missing scenario CSV: {csv_path}. "
                    "Run scripts/03_run_baseline.py and scripts/04_run_predictive.py first."
                )
            runs[key] = pd.read_csv(csv_path)
        for policy in OPTIONAL_POLICIES:
            key = f"{scen}_{policy}"
            csv_path = DATA_DIR / f"{key}.csv"
            if csv_path.exists():
                runs[key] = pd.read_csv(csv_path)
            else:
                print(
                    f"[05] (optional) {csv_path.name} not found - "
                    f"skipping {policy} for the {scen} scenario."
                )
    return runs


def main() -> None:
    ensure_dirs()
    runs = _load_runs()

    summaries = [summarize_experiment(df) for df in runs.values()]
    summary_df = build_summary_table(summaries)
    summary_csv = write_csv(summary_df, RESULTS_DIR / "summary.csv")
    print(f"[05] Wrote per-experiment summary -> {summary_csv}")
    print(summary_df.to_string(index=False))

    deltas_df = compute_deltas(summary_df)
    deltas_csv = write_csv(deltas_df, RESULTS_DIR / "deltas.csv")
    print(f"\n[05] Wrote per-policy deltas vs static -> {deltas_csv}")
    if not deltas_df.empty:
        print(deltas_df.to_string(index=False))

    figs: List[Path] = []
    for scen in SCENARIOS:
        group: Dict[str, pd.DataFrame] = {}
        for policy in (*REQUIRED_POLICIES, *OPTIONAL_POLICIES):
            key = f"{scen}_{policy}"
            if key in runs:
                group[policy] = runs[key]
        figs.append(
            plot_latency_timeseries(
                group,
                FIGURES_DIR / f"latency_timeseries_{scen}.png",
                title=f"Latency over time ({scen} scenario)",
            )
        )
        figs.append(
            plot_throughput_timeseries(
                group,
                FIGURES_DIR / f"throughput_timeseries_{scen}.png",
                title=f"Throughput over time ({scen} scenario)",
            )
        )
        figs.append(
            plot_utilization_timeseries(
                group,
                FIGURES_DIR / f"utilization_timeseries_{scen}.png",
                title=f"Utilization over time ({scen} scenario)",
            )
        )

    for metric, ylabel, title in (
        ("avg_latency_ms", "ms", "Average latency"),
        ("p95_latency_ms", "ms", "p95 latency"),
        ("avg_throughput_mbps", "Mbps", "Average delivered throughput"),
        ("avg_packet_loss_pct", "%", "Average packet loss"),
        ("congestion_event_duration_pct", "% of ticks", "Congestion event duration"),
    ):
        figs.append(
            plot_metric_bars(
                summary_df,
                FIGURES_DIR / f"bars_{metric}.png",
                metric=metric,
                title=title,
                ylabel=ylabel,
            )
        )

    print(f"\n[05] Rendered {len(figs)} figures into {FIGURES_DIR}")


if __name__ == "__main__":
    main()
