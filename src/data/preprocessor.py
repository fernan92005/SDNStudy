"""Sliding-window dataset construction + MinMax scaling for the LSTM.

Given a per-tick / per-link DataFrame, we produce one supervised-learning
dataset per link: each sample is `(window_size x n_features) -> horizon`
predicted utilizations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import numpy as np
import pandas as pd
from sklearn.preprocessing import MinMaxScaler

from src.config import CONFIG, MLConfig


@dataclass
class WindowedDataset:
    X: np.ndarray
    y: np.ndarray
    feature_columns: Tuple[str, ...]
    target_column: str
    window_size: int
    horizon: int
    scaler: MinMaxScaler
    target_scaler: MinMaxScaler

    def split(
        self, train_frac: float, val_frac: float
    ) -> Tuple[
        Tuple[np.ndarray, np.ndarray],
        Tuple[np.ndarray, np.ndarray],
        Tuple[np.ndarray, np.ndarray],
    ]:
        n = self.X.shape[0]
        n_train = int(n * train_frac)
        n_val = int(n * val_frac)
        n_test = n - n_train - n_val
        if n_test < 1:
            n_val = max(0, n_val - 1)
            n_test = n - n_train - n_val

        return (
            (self.X[:n_train], self.y[:n_train]),
            (self.X[n_train : n_train + n_val], self.y[n_train : n_train + n_val]),
            (self.X[n_train + n_val :], self.y[n_train + n_val :]),
        )


def _per_link_frames(df: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    out: Dict[str, pd.DataFrame] = {}
    for link, group in df.groupby("link"):
        out[link] = group.sort_values("tick").reset_index(drop=True)
    return out


def build_windows(
    df: pd.DataFrame,
    ml_cfg: MLConfig | None = None,
    fit_scalers: bool = True,
    feature_scaler: MinMaxScaler | None = None,
    target_scaler: MinMaxScaler | None = None,
) -> WindowedDataset:
    """Stack windows across all links in the DataFrame.

    Each link is treated as an independent multivariate time series; samples
    from different links are concatenated into a single training set so the
    LSTM learns a per-link forecaster shared across links.
    """
    ml_cfg = ml_cfg or CONFIG.ml
    feature_cols = list(ml_cfg.feature_columns)
    target_col = ml_cfg.target_column
    window_size = ml_cfg.window_size
    horizon = ml_cfg.horizon

    per_link = _per_link_frames(df)

    feature_stack: List[np.ndarray] = []
    target_stack: List[np.ndarray] = []

    for link, link_df in per_link.items():
        feats = link_df[feature_cols].to_numpy(dtype=np.float32)
        target = link_df[[target_col]].to_numpy(dtype=np.float32)

        if feats.shape[0] < window_size + horizon:
            continue

        feature_stack.append(feats)
        target_stack.append(target)

    if not feature_stack:
        raise ValueError(
            "Not enough data to build windows. "
            f"Need at least {window_size + horizon} ticks per link."
        )

    all_feats = np.concatenate(feature_stack, axis=0)
    all_targets = np.concatenate(target_stack, axis=0)

    if fit_scalers or feature_scaler is None:
        feature_scaler = MinMaxScaler()
        feature_scaler.fit(all_feats)
    if fit_scalers or target_scaler is None:
        target_scaler = MinMaxScaler()
        target_scaler.fit(all_targets)

    X_chunks: List[np.ndarray] = []
    y_chunks: List[np.ndarray] = []
    for feats, target in zip(feature_stack, target_stack):
        feats_s = feature_scaler.transform(feats)
        target_s = target_scaler.transform(target).reshape(-1)

        for start in range(feats_s.shape[0] - window_size - horizon + 1):
            X_chunks.append(feats_s[start : start + window_size])
            y_chunks.append(
                target_s[start + window_size : start + window_size + horizon]
            )

    X = np.stack(X_chunks, axis=0).astype(np.float32)
    y = np.stack(y_chunks, axis=0).astype(np.float32)

    return WindowedDataset(
        X=X,
        y=y,
        feature_columns=tuple(feature_cols),
        target_column=target_col,
        window_size=window_size,
        horizon=horizon,
        scaler=feature_scaler,
        target_scaler=target_scaler,
    )


def latest_window(
    link_history: pd.DataFrame,
    feature_scaler: MinMaxScaler,
    feature_cols: Sequence[str],
    window_size: int,
) -> np.ndarray | None:
    """Extract the most recent `window_size` rows for inference.

    Returns None if there is not enough history yet.
    """
    if link_history.shape[0] < window_size:
        return None
    recent = link_history[list(feature_cols)].to_numpy(dtype=np.float32)[-window_size:]
    return feature_scaler.transform(recent).astype(np.float32)[np.newaxis, ...]
