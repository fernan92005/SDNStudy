"""Stage 1: simulate mixed normal + burst traffic and write training CSV.

The output (data/training.csv) is what `02_train_model.py` consumes to fit
the LSTM forecaster.
"""
from __future__ import annotations

import _bootstrap  # noqa: F401

from pathlib import Path

from src.config import CONFIG, DATA_DIR, ensure_dirs
from src.data.collector import records_to_dataframe, write_csv
from src.simulation.simulator import Simulator
from src.simulation.topology import build_topology, summarize
from src.simulation.traffic_generator import mixed_training_scenario
from src.sdn.router_static import make_static_router


OUT_CSV: Path = DATA_DIR / "training.csv"


def main() -> Path:
    ensure_dirs()
    print("[01] Building topology...")
    g = build_topology()
    print(summarize(g))

    print("[01] Generating mixed normal + burst training scenario...")
    scenario = mixed_training_scenario()

    print("[01] Running simulator under static routing to collect training data...")
    policy = make_static_router("h1", "h2", g)
    sim = Simulator(
        graph=g,
        scenario=scenario,
        policy=policy,
        policy_name="static",
        source="h1",
        destination="h2",
    )
    records = sim.run()
    df = records_to_dataframe(records)
    write_csv(df, OUT_CSV)
    print(f"[01] Wrote {len(df):,} rows -> {OUT_CSV}")
    return OUT_CSV


if __name__ == "__main__":
    main()
