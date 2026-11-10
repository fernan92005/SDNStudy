"""Inference helper: load saved LSTM + scalers, forecast next-horizon utilization.

Used by the predictive router to flag impending congestion based on the most
recent metric window for each link.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Sequence, Tuple

import numpy as np
import pandas as pd

from src.config import CONFIG, MODELS_DIR
from src.data.preprocessor import latest_window
from src.ml.train import (
    FEATURE_SCALER_PATH,
    METADATA_PATH,
    MODEL_PATH,
    TARGET_SCALER_PATH,
)


@dataclass
class LoadedForecaster:
    model: object
    feature_scaler: object
    target_scaler: object
    feature_columns: Tuple[str, ...]
    target_column: str
    window_size: int
    horizon: int

    def predict_link(
        self,
        link_history: pd.DataFrame,
    ) -> np.ndarray | None:
        """Return horizon-step forecast of utilization for one link.

        Returns None if there isn't enough history yet.
        """
        win = latest_window(
            link_history,
            self.feature_scaler,
            self.feature_columns,
            self.window_size,
        )
        if win is None:
            return None
        pred_s = self.model.predict(win, verbose=0)
        pred = self.target_scaler.inverse_transform(pred_s)[0]
        return pred.astype(np.float32)


def load_forecaster(
    model_path: Path = MODEL_PATH,
    feature_scaler_path: Path = FEATURE_SCALER_PATH,
    target_scaler_path: Path = TARGET_SCALER_PATH,
    metadata_path: Path = METADATA_PATH,
) -> LoadedForecaster:
    import joblib
    import tensorflow as tf

    if not model_path.exists():
        raise FileNotFoundError(
            f"Trained model not found at {model_path}. "
            "Run scripts/02_train_model.py first."
        )

    model = tf.keras.models.load_model(model_path)
    feature_scaler = joblib.load(feature_scaler_path)
    target_scaler = joblib.load(target_scaler_path)
    metadata = json.loads(metadata_path.read_text())

    return LoadedForecaster(
        model=model,
        feature_scaler=feature_scaler,
        target_scaler=target_scaler,
        feature_columns=tuple(metadata["feature_columns"]),
        target_column=metadata["target_column"],
        window_size=int(metadata["window_size"]),
        horizon=int(metadata["horizon"]),
    )


def will_congest(
    forecast: np.ndarray | None,
    threshold: float | None = None,
) -> bool:
    """Return True if the forecast contains any value above the threshold."""
    if forecast is None or forecast.size == 0:
        return False
    threshold = threshold if threshold is not None else CONFIG.ml.congestion_threshold
    return bool(np.any(forecast >= threshold))
