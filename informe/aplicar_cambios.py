#!/usr/bin/env python3
"""Aplica los cambios de informe.patch sin usar git.

Uso, desde la carpeta del repo:   python3 informe/aplicar_cambios.py
Se puede ejecutar varias veces: si un cambio ya esta hecho, lo salta.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

CAMBIOS = {
    "mininet/ryu_app.py": [
        ("FORECASTER = os.environ.get",
         'WINDOW_SIZE = 20  # must match CONFIG.ml.window_size\n',
         'WINDOW_SIZE = 20  # must match CONFIG.ml.window_size\n'
         '# [informe] FORECASTER=lstm (por defecto) o persistence (referencia trivial:\n'
         '# repite la ultima utilizacion medida en los 5 pasos del horizonte).\n'
         'FORECASTER = os.environ.get("FORECASTER", "lstm").lower()\n'),
        ('if FORECASTER == "persistence":',
         '            forecast = self._forecaster.predict_link(df)\n',
         '            if FORECASTER == "persistence":\n'
         '                import numpy as np\n'
         '                forecast = np.full(\n'
         '                    self._forecaster.horizon,\n'
         '                    float(df["utilization"].iloc[-1]), dtype=np.float32,\n'
         '                )\n'
         '            else:\n'
         '                forecast = self._forecaster.predict_link(df)\n'),
        ("self._log_decision(hist[-1]",
         '        decision = self._engine.step([forecast])\n',
         '        decision = self._engine.step([forecast])\n'
         '        self._log_decision(hist[-1]["utilization"], forecast, decision)\n'),
        ("def _log_decision(",
         '    # ---- flow installation -------------------------------------------------\n',
         '    def _log_decision(self, util_now, forecast, decision) -> None:\n'
         '        """[informe] Una fila por tick en runtime/decisions_<MODE>_<FORECASTER>.csv\n'
         '        y una linea de log cuando la prediccion pasa el umbral o cambia la accion."""\n'
         '        import csv\n'
         '        peak = float(decision.predicted_peak_utilization)\n'
         '        action = decision.action.value\n'
         '        path_csv = RUNTIME_DIR / f"decisions_{MODE}_{FORECASTER}.csv"\n'
         '        new = not path_csv.exists()\n'
         '        with path_csv.open("a", newline="") as fh:\n'
         '            w = csv.writer(fh)\n'
         '            if new:\n'
         '                w.writerow(["ts_epoch", "tick", "util_s1s2", "forecast",\n'
         '                            "pred_peak", "action", "reason"])\n'
         '            w.writerow([f"{time.time():.3f}", self.tick, f"{util_now:.3f}",\n'
         '                        " ".join(f"{v:.3f}" for v in forecast),\n'
         '                        f"{peak:.3f}", action, decision.reason])\n'
         '        prev = getattr(self, "_last_action", None)\n'
         '        if peak >= 0.75 or action != prev:\n'
         '            self.logger.info(\n'
         '                "[tick %d] util s1-s2=%.2f | prediccion t+1..t+5=[%s] pico=%.2f -> %s (%s)",\n'
         '                self.tick, util_now, " ".join(f"{v:.2f}" for v in forecast),\n'
         '                peak, action, decision.reason,\n'
         '            )\n'
         '        self._last_action = action\n'
         '\n'
         '    # ---- flow installation -------------------------------------------------\n'),
    ],
    "mininet/collect_metrics.py": [
        ('"ts_epoch",  # [informe]',
         '        "effective_latency_ms", "effective_throughput_mbps", "effective_packet_loss_pct",\n    ]',
         '        "effective_latency_ms", "effective_throughput_mbps", "effective_packet_loss_pct",\n'
         '        "ts_epoch",  # [informe] para alinear con el log del controlador y con iperf3\n    ]'),
        ('"ts_epoch": f"{tick_t_lo',
         '                    "tick": tick,\n',
         '                    "tick": tick,\n'
         '                    "ts_epoch": f"{tick_t_lo:.3f}",\n'),
    ],
}

ok = True
for rel, cambios in CAMBIOS.items():
    p = ROOT / rel
    if not p.exists():
        print(f"ERROR: no encuentro {p}. Ejecuta esto desde la carpeta del repo.")
        sys.exit(1)
    raw = p.read_bytes()
    crlf = b"\r\n" in raw
    s = raw.decode("utf-8").replace("\r\n", "\n")
    for marca, viejo, nuevo in cambios:
        if marca in s:
            print(f"  ya aplicado: {rel} ({marca})")
        elif s.count(viejo) == 1:
            s = s.replace(viejo, nuevo, 1)
            print(f"  aplicado:    {rel} ({marca})")
        else:
            ok = False
            print(f"  NO ENCONTRADO en {rel}: {viejo.strip()[:60]!r} (aparece {s.count(viejo)} veces)")
    if crlf:
        s = s.replace("\n", "\r\n")
    p.write_bytes(s.encode("utf-8"))

if ok:
    import py_compile
    for rel in CAMBIOS:
        py_compile.compile(str(ROOT / rel), doraise=True)
    print("\nListo. Los dos archivos compilan bien.")
else:
    print("\nAlgun cambio no se ha podido aplicar. Pasale a Claude esta salida.")
    sys.exit(1)
