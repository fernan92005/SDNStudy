#!/usr/bin/env python3
"""LSTM frente a referencias triviales en el simulador del repo (Track A).

Uso, desde la carpeta del repo y despues de scripts/run_all.py:

    .venv/bin/python informe/persistencia_sim.py

1) Error de prediccion en el conjunto de test del propio repo y en la traza de
   congestion (enlace s1-s2 con encaminamiento estatico, sin desvios).
2) Prediccion de la congestion como evento: etiqueta y_t = max(u_{t+1..t+5}) >= 0,75,
   alarma = pico predicho >= 0,75. Precision, recall, F1 y antelacion por rafaga.
3) Lazo cerrado: el mismo router y la misma regla de decision, cambiando solo el
   predictor. Metricas del evaluador del repo + cambios de ruta.

Escribe informe_persistencia/{error_prediccion,evento,lazo_cerrado}.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from src.config import CONFIG, DATA_DIR
from src.data.collector import records_to_dataframe
from src.data.preprocessor import build_windows
from src.evaluation.metrics import summarize_experiment
from src.ml.predict import load_forecaster
from src.sdn.router_predictive import make_predictive_router
from src.simulation.simulator import Simulator
from src.simulation.topology import build_topology
from src.simulation.traffic_generator import congestion_scenario, normal_scenario

H = CONFIG.ml.horizon
W = CONFIG.ml.window_size
THR = CONFIG.decision.congestion_threshold
UTIL_IDX = list(CONFIG.ml.feature_columns).index("utilization")
OUT = ROOT / "informe_persistencia"


class TrivialForecaster:
    """Misma interfaz que LoadedForecaster, sin red neuronal."""

    def __init__(self, base, kind: str, k: int = 5):
        self.feature_columns = base.feature_columns
        self.window_size = base.window_size
        self.horizon = base.horizon
        self.kind, self.k = kind, k

    def predict_link(self, df: pd.DataFrame):
        if df.shape[0] < self.window_size:
            return None
        u = df["utilization"].to_numpy(dtype=np.float32)
        v = u[-1] if self.kind == "persistence" else u[-self.k:].mean()
        return np.full(self.horizon, v, dtype=np.float32)


# ------------------------------------------------------------ 1) error

def trivial_from_windows(X_raw: np.ndarray, kind: str) -> np.ndarray:
    u = X_raw[:, :, UTIL_IDX]
    v = u[:, -1] if kind == "persistence" else u[:, -5:].mean(axis=1)
    return np.repeat(v[:, None], H, axis=1)


def errors(fc, name: str, df: pd.DataFrame, split_test: bool) -> list[dict]:
    ds = build_windows(df, fit_scalers=False, feature_scaler=fc.feature_scaler,
                       target_scaler=fc.target_scaler)
    X, y = ds.X, ds.y
    if split_test:
        _, _, (X, y) = ds.split(CONFIG.ml.train_split, CONFIG.ml.val_split)
    y_true = fc.target_scaler.inverse_transform(y.reshape(-1, 1)).reshape(y.shape)
    X_raw = fc.feature_scaler.inverse_transform(X.reshape(-1, X.shape[2])).reshape(X.shape)
    preds = {
        "LSTM": fc.target_scaler.inverse_transform(
            fc.model.predict(X, verbose=0).reshape(-1, 1)).reshape(y.shape),
        "Persistencia": trivial_from_windows(X_raw, "persistence"),
        "Media movil (5 s)": trivial_from_windows(X_raw, "mean"),
    }
    rows = []
    for m, p in preds.items():
        e = p - y_true
        rows.append({"datos": name, "modelo": m, "muestras": len(y_true),
                     "MAE": float(np.abs(e).mean()),
                     "RMSE": float(np.sqrt((e ** 2).mean())),
                     "MAE_t+5": float(np.abs(e[:, -1]).mean())})
    return rows, preds, y_true


# ------------------------------------------------------------ 2) evento

def event_metrics(preds: dict, y_true: np.ndarray, start_tick: int, util: pd.Series) -> list[dict]:
    label = y_true.max(axis=1) >= THR
    rows = []
    for m, p in preds.items():
        alarm = p.max(axis=1) >= THR
        tp = int((alarm & label).sum()); fp = int((alarm & ~label).sum())
        fn = int((~alarm & label).sum())
        prec = tp / (tp + fp) if tp + fp else float("nan")
        rec = tp / (tp + fn) if tp + fn else float("nan")
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else float("nan")
        # antelacion: primera alarma respecto al inicio de cada rafaga.
        # La muestra i usa datos hasta el tick start_tick + i - 1 y predice desde start_tick + i.
        ticks = start_tick + np.arange(len(alarm)) - 1
        leads, leads_x = [], []
        for s, e in CONFIG.traffic.burst_windows:
            a = ticks[(alarm) & (ticks >= s - 20) & (ticks < e)]
            seg = util.loc[s - 5:e]
            cross = int(seg[seg >= THR].index[0])
            leads.append(int(s - a[0]) if len(a) else None)
            leads_x.append(int(cross - a[0]) if len(a) else None)
        rows.append({"modelo": m, "precision": prec, "recall": rec, "F1": f1,
                     "falsas_alarmas": fp, "congestiones_perdidas": fn,
                     "antelacion_s (+=antes)": " / ".join(
                         "-" if v is None else f"{v:+d}" for v in leads),
                     "antelacion_vs_cruce_0.75_s": " / ".join(
                         "-" if v is None else f"{v:+d}" for v in leads_x)})
    return rows


# ------------------------------------------------------------ 3) lazo cerrado

def closed_loop(fc_by_name: dict) -> list[dict]:
    g = build_topology()
    rows = []
    for scen_builder, scen in ((normal_scenario, "normal"), (congestion_scenario, "congestion")):
        for name, fc in fc_by_name.items():
            log: list = []
            pol = make_predictive_router("h1", "h2", g, fc, log=log)
            sim = Simulator(graph=g, scenario=scen_builder(), policy=pol,
                            policy_name="predictive", source="h1", destination="h2")
            df = records_to_dataframe(sim.run())
            s = summarize_experiment(df)
            paths = [r["chosen_path"] for r in log]
            changes = sum(1 for i in range(1, len(paths)) if paths[i] != paths[i - 1])
            first = next((r["tick"] for r in log if r["action"] == "reroute_traffic"), None)
            rows.append({"escenario": scen, "predictor": name,
                         "lat_media_ms": s.avg_latency_ms, "lat_p95_ms": s.p95_latency_ms,
                         "throughput_mbps": s.avg_throughput_mbps,
                         "perdidas_pct": s.avg_packet_loss_pct,
                         "congestion_pct": s.congestion_event_duration_pct,
                         "cambios_ruta": changes,
                         "ticks_alternativo": sum("s3" in p for p in paths),
                         "primer_desvio_tick": first})
            print(f"  {scen:10s} {name:18s} media={s.avg_latency_ms:6.2f} "
                  f"p95={s.p95_latency_ms:6.2f} cambios={changes}")
    return rows


def main() -> None:
    OUT.mkdir(exist_ok=True)
    fc = load_forecaster()

    err_rows, ev_rows = [], []
    train = pd.read_csv(DATA_DIR / "training.csv")
    r, _, _ = errors(fc, "test del repo (training.csv, 15 % final)", train, split_test=True)
    err_rows += r

    cs = pd.read_csv(DATA_DIR / "congestion_static.csv")
    cs = cs[cs["link"].isin(["s1-s2", "s2-s1"])]
    r, preds, y_true = errors(fc, "traza de congestion, enlace s1-s2", cs, split_test=False)
    err_rows += r
    ev_rows = event_metrics(preds, y_true, start_tick=int(cs["tick"].min()) + W,
                           util=cs.set_index("tick")["utilization"])

    pd.set_option("display.width", 200)
    e = pd.DataFrame(err_rows); e.to_csv(OUT / "error_prediccion.csv", index=False)
    print("\n== Error de prediccion (utilizacion) ==\n", e.round(4).to_string(index=False))
    v = pd.DataFrame(ev_rows); v.to_csv(OUT / "evento.csv", index=False)
    print("\n== Congestion como evento (enlace s1-s2, estatico) ==\n", v.round(3).to_string(index=False))

    if "--sin-lazo" in sys.argv:
        return
    print("\n== Lazo cerrado ==")
    loop = pd.DataFrame(closed_loop({
        "LSTM": fc,
        "Persistencia": TrivialForecaster(fc, "persistence"),
        "Media movil (5 s)": TrivialForecaster(fc, "mean"),
    }))
    loop.to_csv(OUT / "lazo_cerrado.csv", index=False)
    print(loop.round(2).to_string(index=False))


if __name__ == "__main__":
    main()
