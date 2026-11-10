"""Stage 3b: ECMP baseline — equal-cost multi-path routing.

Runs both the normal and the congestion scenario under a fixed ECMP policy
that splits the offered load equally across all available simple paths
between source and destination. Writes one CSV per scenario into data/.

This is the second "WITHOUT-AI" baseline; together with the static
shortest-path baseline (03_run_baseline.py) it brackets what classic
network engineering achieves before any ML is introduced.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

from pathlib import Path
from typing import Dict

from src.config import DATA_DIR, ensure_dirs
from src.data.collector import records_to_dataframe, write_csv
from src.sdn.router_ecmp import make_ecmp_router
from src.simulation.simulator import Simulator
from src.simulation.topology import build_topology
from src.simulation.traffic_generator import congestion_scenario, normal_scenario


def main() -> Dict[str, Path]:
    ensure_dirs()
    g = build_topology()
    policy = make_ecmp_router("h1", "h2", g, max_paths=2)
    outputs: Dict[str, Path] = {}

    for scenario_builder, label in (
        (normal_scenario, "normal_ecmp"),
        (congestion_scenario, "congestion_ecmp"),
    ):
        scenario = scenario_builder()
        sim = Simulator(
            graph=g,
            scenario=scenario,
            policy=policy,
            policy_name="ecmp",
            source="h1",
            destination="h2",
        )
        df = records_to_dataframe(sim.run())
        out = DATA_DIR / f"{label}.csv"
        write_csv(df, out)
        outputs[label] = out
        print(f"[03b] {label}: {len(df):,} rows -> {out}")

    return outputs


if __name__ == "__main__":
    main()
