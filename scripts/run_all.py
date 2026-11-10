"""End-to-end driver: dataset -> training -> baseline -> predictive -> evaluate."""
from __future__ import annotations

import _bootstrap  # noqa: F401

import importlib
import time
from typing import List, Tuple


STAGES: List[Tuple[str, str]] = [
    ("01_generate_dataset", "Generate training dataset"),
    ("02_train_model", "Train LSTM forecaster"),
    ("03_run_baseline", "Run WITHOUT-AI baseline (static routing)"),
    ("03b_run_ecmp", "Run WITHOUT-AI baseline (ECMP multi-path)"),
    ("04_run_predictive", "Run WITH-AI predictive routing"),
    ("05_evaluate", "Aggregate metrics, compare, plot"),
]


def main() -> None:
    overall = time.perf_counter()
    for module_name, description in STAGES:
        start = time.perf_counter()
        print("\n" + "=" * 70)
        print(f"  {description}  ({module_name}.py)")
        print("=" * 70)
        module = importlib.import_module(module_name)
        module.main()
        print(f"-- {module_name} took {time.perf_counter() - start:.1f}s")
    print(
        "\n"
        f"All stages complete in {time.perf_counter() - overall:.1f}s. "
        "See results/summary.csv and results/figures/."
    )


if __name__ == "__main__":
    main()
