#!/usr/bin/env python3
"""Metricas extra para Resultados y Analisis critico (predictive-sdn-routing).

Uso (desde la carpeta del repo, con el entorno .venv):

    .venv/bin/python informe/analisis_informe.py resultados \
        --track-a results_trackA/summary.csv \
        --track-a-decisions results_trackA/congestion_predictive_decisions.json

Espera una subcarpeta por ejecucion dentro de `resultados/`:

    resultados/normal_static/        metrics.csv  controller.log  iperf_*.json
    resultados/normal_predictive/    metrics.csv  decisions.csv   controller.log  iperf_*.json
    resultados/congestion_static/    ...
    resultados/congestion_predictive/ ... (+ ctrl.pcap si se capturo el canal de control)
    resultados/congestion_persistence/ (opcional, FORECASTER=persistence)

Escribe en `resultados/`:
    tabla_extra.csv            una fila por ejecucion
    tabla_extra.tex            la misma tabla en LaTeX (booktabs)
    linea_temporal_<run>.png   utilizacion, prediccion, umbrales y camino activo
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
from pathlib import Path

import numpy as np
import pandas as pd

THRESHOLD = 0.75
CLEAR = 0.65
BURSTS = ((120, 200), (320, 410), (500, 560))  # segundos desde el inicio del cliente


# ---------------------------------------------------------------- lectura

def per_tick(metrics: pd.DataFrame) -> pd.DataFrame:
    """Una fila por tick con las mismas reglas que src/evaluation/metrics.py."""
    act = metrics[metrics["is_on_active_path"].astype(str).str.lower() == "true"]
    if act.empty:
        act = metrics
    g = act.groupby("tick").agg(
        utilization=("utilization", "max"),
        throughput=("carried_load_mbps", "min"),
    )
    first = metrics.groupby("tick").first()
    g["latency_ms"] = first["effective_latency_ms"]
    g["throughput"] = first["effective_throughput_mbps"].where(
        first["effective_throughput_mbps"] > 0, g["throughput"])
    g["path_index"] = first["path_index"]
    if "ts_epoch" in first:
        g["ts_epoch"] = first["ts_epoch"].astype(float)
    # Enlaces de entrada y salida para estimar perdidas por diferencia de caudal
    for link, col in (("h1-s1", "in_mbps"), ("s2-h2", "out_mbps")):
        sub = metrics[metrics["link"] == link].set_index("tick")["throughput_mbps"]
        g[col] = sub
    return g


def iperf_loss(run_dir: Path) -> tuple[float | None, float | None, list[float]]:
    """Perdidas UDP segun iperf3 (todas las sesiones del cliente) e inicio de rafagas."""
    lost = pkts = 0
    burst_starts: list[float] = []
    found = False
    for f in sorted(run_dir.glob("iperf_client_*.json")):
        try:
            d = json.loads(f.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        s = d.get("end", {}).get("sum", {})
        if "lost_packets" in s and "packets" in s:
            lost += int(s["lost_packets"])
            pkts += int(s["packets"])
            found = True
        if "burst" in f.name:
            ts = d.get("start", {}).get("timestamp", {}).get("timesecs")
            if ts:
                burst_starts.append(float(ts))
    pct = (100.0 * lost / pkts) if (found and pkts) else None
    return pct, (float(lost) if found else None), sorted(burst_starts)


def pcap_rate(pcap: Path) -> tuple[float, float] | None:
    """Bytes/s y paquetes/s en una captura pcap clasica (tcpdump -w)."""
    if not pcap.exists():
        return None
    data = pcap.read_bytes()
    if len(data) < 24:
        return None
    magic = struct.unpack("<I", data[:4])[0]
    endian = "<" if magic in (0xA1B2C3D4, 0xA1B23C4D) else ">"
    nano = magic in (0xA1B23C4D, 0x4D3CB2A1)
    off, n, total, t0, t1 = 24, 0, 0, None, None
    while off + 16 <= len(data):
        sec, frac, incl, orig = struct.unpack(endian + "IIII", data[off:off + 16])
        t = sec + frac / (1e9 if nano else 1e6)
        t0 = t if t0 is None else t0
        t1 = t
        n += 1
        total += orig
        off += 16 + incl
    if not n or t1 == t0:
        return None
    dur = t1 - t0
    return total / dur, n / dur


# ---------------------------------------------------------------- metricas

def route_changes(path_index: pd.Series) -> int:
    p = path_index.dropna().astype(int).to_numpy()
    return int((p[1:] != p[:-1]).sum()) if len(p) > 1 else 0


def lead_times(dec: pd.DataFrame, burst_starts: list[float]) -> list[float | None]:
    """Segundos entre el primer REROUTE y el inicio real de cada rafaga.
    Positivo = avisa antes de que empiece; negativo = reacciona tarde."""
    out: list[float | None] = []
    rr = dec[dec["action"] == "reroute_traffic"]
    for b in burst_starts:
        win = rr[(rr["ts_epoch"] >= b - 30) & (rr["ts_epoch"] <= b + 60)]
        out.append(round(b - float(win["ts_epoch"].iloc[0]), 1) if not win.empty else None)
    return out


def analyse_run(run_dir: Path) -> tuple[dict, pd.DataFrame | None, pd.DataFrame | None, list[float]]:
    name = run_dir.name
    m = pd.read_csv(run_dir / "metrics.csv")
    t = per_tick(m)
    lat = t["latency_ms"]
    lat = lat[lat > 0]
    row = {
        "run": name,
        "lat_media_ms": lat.mean(),
        "lat_p50_ms": lat.quantile(0.50),
        "lat_p95_ms": lat.quantile(0.95),
        "lat_p99_ms": lat.quantile(0.99),
        "lat_max_ms": lat.max(),
        "throughput_mbps": t["throughput"].mean(),
        "congestion_pct": 100.0 * (t["utilization"] >= 0.9).mean(),
        "cambios_ruta": route_changes(t["path_index"]),
        "ticks_alternativo": int((t["path_index"] == 1).sum()),
        "perdida_diferencia_mbps": (t["in_mbps"] - t["out_mbps"]).clip(lower=0).mean(),
    }
    loss_pct, lost, bursts = iperf_loss(run_dir)
    row["perdida_iperf_pct"] = loss_pct
    row["paquetes_perdidos_iperf"] = lost

    # Latencia solo dentro de las rafagas (alineada por reloj si hay ts_epoch)
    if bursts and "ts_epoch" in t:
        mask = np.zeros(len(t), bool)
        for b, (s, e) in zip(bursts, BURSTS):
            mask |= ((t["ts_epoch"] >= b) & (t["ts_epoch"] < b + (e - s))).to_numpy()
        lb = t.loc[mask, "latency_ms"]
        lb = lb[lb > 0]
        if not lb.empty:
            row["lat_media_rafagas_ms"] = lb.mean()
            row["lat_p95_rafagas_ms"] = lb.quantile(0.95)

    dec = None
    dec_path = run_dir / "decisions.csv"
    if dec_path.exists():
        dec = pd.read_csv(dec_path)
        a = (dec["action"] == "reroute_traffic").astype(int).to_numpy()
        row["cambios_decision"] = int((a[1:] != a[:-1]).sum())
        row["ticks_reroute_decision"] = int(a.sum())
        if bursts:
            lt = lead_times(dec, bursts)
            row["antelacion_rafagas_s"] = " / ".join("-" if v is None else f"{v:+.0f}" for v in lt)
        # Duracion de las estancias en cada camino (detecta el ciclo 5+1)
        runs, cur, n = [], a[0] if len(a) else 0, 0
        for v in a:
            if v == cur:
                n += 1
            else:
                runs.append((cur, n)); cur, n = v, 1
        runs.append((cur, n))
        alt = [n for v, n in runs if v == 1]
        if alt:
            row["estancia_media_alt_s"] = float(np.mean(alt))

    rate = pcap_rate(run_dir / "ctrl.pcap")
    if rate:
        row["canal_control_kbps"] = rate[0] * 8 / 1000
        row["canal_control_pps"] = rate[1]
    return row, t, dec, bursts


def track_a_rows(summary_csv: Path, decisions_json: Path | None) -> list[dict]:
    s = pd.read_csv(summary_csv)
    rows = []
    for _, r in s.iterrows():
        rows.append({
            "run": f"sim_{r['scenario']}_{r['policy']}",
            "lat_media_ms": r["avg_latency_ms"],
            "lat_p95_ms": r["p95_latency_ms"],
            "throughput_mbps": r["avg_throughput_mbps"],
            "perdida_sim_pct": r["avg_packet_loss_pct"],
            "congestion_pct": r["congestion_event_duration_pct"],
        })
    if decisions_json and decisions_json.exists():
        d = json.loads(decisions_json.read_text())
        p = [x["chosen_path"] for x in d]
        ch = sum(1 for i in range(1, len(p)) if p[i] != p[i - 1])
        for r in rows:
            if r["run"] == "sim_congestion_predictive":
                r["cambios_ruta"] = ch
                r["ticks_alternativo"] = sum("s3" in x for x in p)
            elif r["run"].startswith("sim_congestion"):
                r["cambios_ruta"] = 0
    return rows


# ---------------------------------------------------------------- figura

def plot_timeline(name: str, t: pd.DataFrame, dec: pd.DataFrame | None,
                  bursts: list[float], out: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 5.5), sharex=True,
                                   gridspec_kw={"height_ratios": [3, 1]})
    t0 = float(t["ts_epoch"].iloc[0]) if "ts_epoch" in t else None
    if dec is not None and t0 is not None:
        x = dec["ts_epoch"] - t0
        ax1.plot(x, dec["util_s1s2"], color="#4C72B0", lw=1, label="Utilización medida s1-s2")
        ax1.plot(x, dec["pred_peak"], color="#DD8452", lw=1, label="Pico predicho (t+1..t+5)")
    else:
        ax1.plot(t.index, t["utilization"], color="#4C72B0", lw=1, label="Utilización (camino activo)")
    ax1.axhline(THRESHOLD, color="#C44E52", ls="--", lw=0.8, label="Umbral 0,75")
    ax1.axhline(CLEAR, color="#8172B2", ls=":", lw=0.8, label="Vuelta 0,65")
    if bursts and t0 is not None:
        for b, (s, e) in zip(bursts, BURSTS):
            for ax in (ax1, ax2):
                ax.axvspan(b - t0, b - t0 + (e - s), color="0.9", zorder=0)
    ax1.set_ylabel("Utilización")
    ax1.legend(loc="upper right", fontsize=8, ncol=2)
    xs = (t["ts_epoch"] - t0) if t0 is not None else t.index
    ax2.step(xs, t["path_index"], where="post", color="0.2", lw=1)
    ax2.set_yticks([0, 1], ["principal", "alternativo"])
    ax2.set_xlabel("Tiempo (s)")
    ax1.set_title(name.replace("_", " "))
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------- main

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("resultados", type=Path)
    ap.add_argument("--track-a", type=Path, help="summary.csv de la simulacion (incluye ECMP)")
    ap.add_argument("--track-a-decisions", type=Path)
    args = ap.parse_args()

    rows = []
    for d in sorted(p for p in args.resultados.iterdir() if (p / "metrics.csv").exists()):
        row, t, dec, bursts = analyse_run(d)
        rows.append(row)
        if dec is not None or "congestion" in d.name:
            plot_timeline(d.name, t, dec, bursts, args.resultados / f"linea_temporal_{d.name}.png")
    if args.track_a:
        rows += track_a_rows(args.track_a, args.track_a_decisions)
    if not rows:
        sys.exit(f"No hay subcarpetas con metrics.csv en {args.resultados}")

    df = pd.DataFrame(rows)
    df.to_csv(args.resultados / "tabla_extra.csv", index=False)
    pd.set_option("display.width", 200)
    print(df.round(2).to_string(index=False))

    cols = [c for c in ("run", "lat_media_ms", "lat_p95_ms", "throughput_mbps",
                        "perdida_iperf_pct", "congestion_pct", "cambios_ruta")
            if c in df]
    def fmt(v):
        if isinstance(v, str):
            return v.replace("_", r"\_")
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "--"
        return f"{v:.2f}".replace(".", "{,}")
    lines = [r"\begin{tabular}{@{}l" + "r" * (len(cols) - 1) + "@{}}", r"\toprule",
             " & ".join(c.replace("_", r"\_") for c in cols) + r" \\", r"\midrule"]
    lines += [" & ".join(fmt(v) for v in r) + r" \\" for r in df[cols].itertuples(index=False)]
    lines += [r"\bottomrule", r"\end{tabular}", ""]
    tex = "\n".join(lines)
    (args.resultados / "tabla_extra.tex").write_text(tex)
    print(f"\nEscrito {args.resultados / 'tabla_extra.csv'} y tabla_extra.tex")


if __name__ == "__main__":
    main()
