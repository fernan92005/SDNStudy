"""Stage 4: WITH-AI predictive routing.

Runs both scenarios under the predictive router, which forecasts utilization
on the default path each tick and reroutes when impending congestion is
predicted.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

import json
from pathlib import Path
from typing import Dict, List

import pandas as pd

from src.config import DATA_DIR, RESULTS_DIR, ensure_dirs
from src.data.collector import records_to_dataframe, write_csv
from src.ml.predict import load_forecaster
from src.sdn.router_predictive import make_predictive_router
from src.simulation.simulator import Simulator
from src.simulation.topology import build_topology
from src.simulation.traffic_generator import congestion_scenario, normal_scenario


def main() -> Dict[str, Path]:
    ensure_dirs()
    g = build_topology()
    forecaster = load_forecaster()

    outputs: Dict[str, Path] = {}

    for scenario_builder, label in (
        (normal_scenario, "normal_predictive"),
        (congestion_scenario, "congestion_predictive"),
    ):
        scenario = scenario_builder()
        log: List[dict] = []
        policy = make_predictive_router(
            source="h1",
            destination="h2",
            graph=g,
            forecaster=forecaster,
            log=log,
        )
        sim = Simulator(
            graph=g,
            scenario=scenario,
            policy=policy,
            policy_name="predictive",
            source="h1",
            destination="h2",
        )
        df = records_to_dataframe(sim.run())
        out = DATA_DIR / f"{label}.csv"
        write_csv(df, out)
        outputs[label] = out

        log_path = RESULTS_DIR / f"{label}_decisions.json"
        log_path.write_text(json.dumps(log, indent=2))
        reroute_count = sum(1 for r in log if r["action"] == "reroute_traffic")
        print(
            f"[04] {label}: {len(df):,} rows -> {out} "
            f"(reroute decisions: {reroute_count})"
        )

    return outputs


if __name__ == "__main__":
    main()
