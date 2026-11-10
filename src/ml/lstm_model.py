"""Keras LSTM forecaster.

Small, explainable model — two stacked LSTM layers with dropout, followed by
a Dense head that predicts the next `horizon` utilization values.
"""
from __future__ import annotations

from src.config import CONFIG, MLConfig


def build_lstm_model(
    n_features: int,
    ml_cfg: MLConfig | None = None,
):
    """Build a compiled `tf.keras.Model` ready for `.fit()`.

    Lazy-imports TensorFlow so that lightweight code paths (data prep, decision
    engine) don't pay the import cost.
    """
    import tensorflow as tf
    from tensorflow.keras import layers, models, optimizers

    ml_cfg = ml_cfg or CONFIG.ml
    units1, units2 = ml_cfg.lstm_units

    model = models.Sequential(
        [
            layers.Input(shape=(ml_cfg.window_size, n_features)),
            layers.LSTM(units1, return_sequences=True),
            layers.Dropout(ml_cfg.dropout),
            layers.LSTM(units2),
            layers.Dropout(ml_cfg.dropout),
            layers.Dense(ml_cfg.horizon, activation="linear"),
        ],
        name="lstm_congestion_forecaster",
    )
    model.compile(
        optimizer=optimizers.Adam(learning_rate=ml_cfg.learning_rate),
        loss="mse",
        metrics=["mae"],
    )
    return model
