"""Stage 3: WITHOUT-AI baseline — static shortest-path routing.

Runs both the normal scenario and the congestion scenario under a fixed
shortest-path router, writing one CSV per scenario into data/.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

from pathlib import Path
from typing import Dict

from src.config import DATA_DIR, ensure_dirs
from src.data.collector import records_to_dataframe, write_csv
from src.sdn.router_static import make_static_router
from src.simulation.simulator import Simulator
from src.simulation.topology import build_topology
from src.simulation.traffic_generator import congestion_scenario, normal_scenario


def main() -> Dict[str, Path]:
    ensure_dirs()
    g = build_topology()
    policy = make_static_router("h1", "h2", g)
    outputs: Dict[str, Path] = {}

    for scenario_builder, label in (
        (normal_scenario, "normal_static"),
        (congestion_scenario, "congestion_static"),
    ):
        scenario = scenario_builder()
        sim = Simulator(
            graph=g,
            scenario=scenario,
            policy=policy,
            policy_name="static",
            source="h1",
            destination="h2",
        )
        df = records_to_dataframe(sim.run())
        out = DATA_DIR / f"{label}.csv"
        write_csv(df, out)
        outputs[label] = out
        print(f"[03] {label}: {len(df):,} rows -> {out}")

    return outputs


if __name__ == "__main__":
    main()
