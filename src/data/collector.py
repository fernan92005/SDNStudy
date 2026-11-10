"""Convert `TickRecord` streams into tidy DataFrames + CSV files.

The CSV schema is intentionally identical for Track A (pure-Python
simulation) and Track B (Mininet + Ryu) so the LSTM and decision engine work
unchanged regardless of how the data was produced.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Tuple

import pandas as pd

from src.simulation.simulator import TickRecord


COLUMNS: Tuple[str, ...] = (
    "tick",
    "scenario",
    "policy",
    "link",
    "src",
    "dst",
    "offered_load_mbps",
    "carried_load_mbps",
    "capacity_mbps",
    "utilization",
    "latency_ms",
    "packet_loss_pct",
    "throughput_mbps",
    "path_index",
    "is_on_active_path",
    "effective_latency_ms",
    "effective_throughput_mbps",
    "effective_packet_loss_pct",
)


def records_to_dataframe(records: Iterable[TickRecord]) -> pd.DataFrame:
    rows = []
    for r in records:
        rows.append(
            {
                "tick": r.tick,
                "scenario": r.scenario,
                "policy": r.policy,
                "link": f"{r.link[0]}-{r.link[1]}",
                "src": r.link[0],
                "dst": r.link[1],
                "offered_load_mbps": r.offered_load_mbps,
                "carried_load_mbps": r.carried_load_mbps,
                "capacity_mbps": r.capacity_mbps,
                "utilization": r.utilization,
                "latency_ms": r.latency_ms,
                "packet_loss_pct": r.packet_loss_pct,
                "throughput_mbps": r.carried_load_mbps,
                "path_index": r.path_index,
                "is_on_active_path": bool(r.is_on_active_path),
                "effective_latency_ms": float(r.effective_latency_ms),
                "effective_throughput_mbps": float(r.effective_throughput_mbps),
                "effective_packet_loss_pct": float(r.effective_packet_loss_pct),
            }
        )
    df = pd.DataFrame(rows, columns=list(COLUMNS))
    return df


def write_csv(df: pd.DataFrame, path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def read_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    return df
