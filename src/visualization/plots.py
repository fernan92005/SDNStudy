"""Matplotlib comparison plots for the white paper figures."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


def _ensure_dir(path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _active_path_rows(df: pd.DataFrame) -> pd.DataFrame:
    if "is_on_active_path" in df.columns:
        rows = df[df["is_on_active_path"].astype(bool)]
        if not rows.empty:
            return rows
    return df[df["offered_load_mbps"] > 0.0]


def _per_tick_latency(df: pd.DataFrame) -> pd.Series:
    if "effective_latency_ms" in df.columns:
        eff = df.groupby("tick")["effective_latency_ms"].first()
        if not eff.empty and float(eff.max()) > 0:
            return eff
    return _active_path_rows(df).groupby("tick")["latency_ms"].sum()


def plot_latency_timeseries(
    runs: Dict[str, pd.DataFrame],
    out_path: Path,
    title: str = "End-to-end path latency over time",
) -> Path:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for label, df in runs.items():
        per_tick = _per_tick_latency(df)
        ax.plot(per_tick.index, per_tick.values, label=label, linewidth=1.3)
    ax.set_xlabel("Simulation tick (s)")
    ax.set_ylabel("End-to-end latency (ms)")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    out_path = _ensure_dir(out_path)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)
    return out_path


def _per_tick_throughput(df: pd.DataFrame) -> pd.Series:
    if "effective_throughput_mbps" in df.columns:
        eff = df.groupby("tick")["effective_throughput_mbps"].first()
        if not eff.empty and float(eff.max()) > 0:
            return eff
    return _active_path_rows(df).groupby("tick")["carried_load_mbps"].min()


def plot_throughput_timeseries(
    runs: Dict[str, pd.DataFrame],
    out_path: Path,
    title: str = "Per-tick delivered throughput",
) -> Path:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for label, df in runs.items():
        per_tick = _per_tick_throughput(df)
        ax.plot(per_tick.index, per_tick.values, label=label, linewidth=1.3)
    ax.set_xlabel("Simulation tick (s)")
    ax.set_ylabel("Flow throughput (Mbps)")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    out_path = _ensure_dir(out_path)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)
    return out_path


def plot_utilization_timeseries(
    runs: Dict[str, pd.DataFrame],
    out_path: Path,
    title: str = "Per-tick max link utilization on active path",
) -> Path:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    for label, df in runs.items():
        per_tick = _active_path_rows(df).groupby("tick")["utilization"].max()
        ax.plot(per_tick.index, per_tick.values, label=label, linewidth=1.3)
    ax.axhline(1.0, color="red", linestyle="--", alpha=0.5, label="capacity")
    ax.set_xlabel("Simulation tick (s)")
    ax.set_ylabel("Max link utilization (load / capacity)")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    out_path = _ensure_dir(out_path)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)
    return out_path


_POLICY_ORDER = ("static", "ecmp", "predictive")


def _policy_sort_key(name: str) -> Tuple[int, str]:
    try:
        return (_POLICY_ORDER.index(name), name)
    except ValueError:
        return (len(_POLICY_ORDER), name)


def plot_metric_bars(
    summary_df: pd.DataFrame,
    out_path: Path,
    metric: str = "avg_latency_ms",
    title: str = "",
    ylabel: str = "",
) -> Path:
    scenarios = sorted(summary_df["scenario"].unique())
    policies = sorted(summary_df["policy"].unique(), key=_policy_sort_key)
    n_pol = len(policies)

    x = np.arange(len(scenarios))
    group_width = 0.8
    width = group_width / max(1, n_pol)

    fig, ax = plt.subplots(figsize=(8, 4.5))
    for i, policy in enumerate(policies):
        values: List[float] = []
        for scen in scenarios:
            row = summary_df[
                (summary_df["scenario"] == scen) & (summary_df["policy"] == policy)
            ]
            values.append(float(row[metric].iloc[0]) if not row.empty else 0.0)
        offset = (i - (n_pol - 1) / 2.0) * width
        ax.bar(x + offset, values, width, label=policy)

    ax.set_xticks(x)
    ax.set_xticklabels(scenarios)
    ax.set_ylabel(ylabel or metric)
    ax.set_title(title or metric)
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend()
    fig.tight_layout()
    out_path = _ensure_dir(out_path)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)
    return out_path


def plot_prediction_vs_actual(
    actual: np.ndarray,
    predicted: np.ndarray,
    out_path: Path,
    title: str = "LSTM forecast vs actual utilization (test set)",
) -> Path:
    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(actual, label="actual", linewidth=1.0, alpha=0.85)
    ax.plot(predicted, label="predicted", linewidth=1.0, alpha=0.85)
    ax.set_xlabel("Sample index (held-out test set)")
    ax.set_ylabel("Utilization")
    ax.set_title(title)
    ax.grid(True, alpha=0.3)
    ax.legend()
    fig.tight_layout()
    out_path = _ensure_dir(out_path)
    fig.savefig(out_path, dpi=160)
    plt.close(fig)
    return out_path
