"""Train the LSTM forecaster.

Reads the generated training CSV, builds windowed datasets, fits the model
and persists the model + scalers to `models/`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Tuple

import joblib
import numpy as np
import pandas as pd

from src.config import CONFIG, MODELS_DIR
from src.data.preprocessor import WindowedDataset, build_windows
from src.ml.lstm_model import build_lstm_model


MODEL_PATH: Path = MODELS_DIR / "lstm.keras"
FEATURE_SCALER_PATH: Path = MODELS_DIR / "feature_scaler.pkl"
TARGET_SCALER_PATH: Path = MODELS_DIR / "target_scaler.pkl"
METADATA_PATH: Path = MODELS_DIR / "metadata.json"


def train_from_csv(
    csv_path: Path,
    model_path: Path = MODEL_PATH,
    feature_scaler_path: Path = FEATURE_SCALER_PATH,
    target_scaler_path: Path = TARGET_SCALER_PATH,
    metadata_path: Path = METADATA_PATH,
    verbose: int = 1,
) -> Dict[str, float]:
    df = pd.read_csv(csv_path)
    ds: WindowedDataset = build_windows(df)

    (X_tr, y_tr), (X_val, y_val), (X_te, y_te) = ds.split(
        CONFIG.ml.train_split, CONFIG.ml.val_split
    )

    model = build_lstm_model(n_features=X_tr.shape[-1])

    history = model.fit(
        X_tr,
        y_tr,
        validation_data=(X_val, y_val) if X_val.shape[0] > 0 else None,
        epochs=CONFIG.ml.epochs,
        batch_size=CONFIG.ml.batch_size,
        verbose=verbose,
    )

    test_metrics: Dict[str, float] = {}
    if X_te.shape[0] > 0:
        loss, mae = model.evaluate(X_te, y_te, verbose=0)
        test_metrics = {"test_mse": float(loss), "test_mae": float(mae)}

        y_pred_s = model.predict(X_te, verbose=0)
        y_pred = ds.target_scaler.inverse_transform(y_pred_s)
        y_true = ds.target_scaler.inverse_transform(y_te)
        rmse = float(np.sqrt(np.mean((y_pred - y_true) ** 2)))
        test_metrics["test_rmse_utilization"] = rmse

    model_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(model_path)
    joblib.dump(ds.scaler, feature_scaler_path)
    joblib.dump(ds.target_scaler, target_scaler_path)

    metadata = {
        "feature_columns": list(ds.feature_columns),
        "target_column": ds.target_column,
        "window_size": ds.window_size,
        "horizon": ds.horizon,
        "train_samples": int(X_tr.shape[0]),
        "val_samples": int(X_val.shape[0]),
        "test_samples": int(X_te.shape[0]),
        "epochs": CONFIG.ml.epochs,
        "batch_size": CONFIG.ml.batch_size,
        "lstm_units": list(CONFIG.ml.lstm_units),
        "history_loss": [float(x) for x in history.history.get("loss", [])],
        "history_val_loss": [float(x) for x in history.history.get("val_loss", [])],
        **test_metrics,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2))

    return metadata
