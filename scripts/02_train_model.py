"""Stage 2: train the LSTM forecaster on data/training.csv."""
from __future__ import annotations

import _bootstrap  # noqa: F401

import json

from src.config import DATA_DIR, ensure_dirs
from src.ml.train import train_from_csv


def main() -> None:
    ensure_dirs()
    csv_path = DATA_DIR / "training.csv"
    if not csv_path.exists():
        raise SystemExit(
            f"Training CSV not found at {csv_path}. "
            "Run scripts/01_generate_dataset.py first."
        )
    print(f"[02] Training LSTM from {csv_path}...")
    metadata = train_from_csv(csv_path, verbose=2)
    print("[02] Done. Metadata:")
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
