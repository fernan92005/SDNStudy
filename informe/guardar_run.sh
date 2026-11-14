#!/usr/bin/env bash
# Guarda todo lo de una ejecucion en resultados/<nombre>/ y deja runtime/ limpio
# para la siguiente.  Uso:  bash informe/guardar_run.sh congestion_predictive
set -euo pipefail
NAME="${1:?Uso: bash informe/guardar_run.sh <escenario>_<politica>}"
cd "$(dirname "$0")/.."
OUT="resultados/$NAME"
mkdir -p "$OUT"

CSV="data/mininet_${NAME}.csv"
[ -f "$CSV" ] && cp "$CSV" "$OUT/metrics.csv" || echo "AVISO: no encuentro $CSV"

# Log del controlador (lo escribe el tee de la terminal 1)
[ -f controller.log ] && mv controller.log "$OUT/controller.log" || echo "AVISO: no hay controller.log"

# Decisiones del controlador por tick (solo en modo predictivo)
shopt -s nullglob
for f in mininet/runtime/decisions_*.csv; do sudo mv "$f" "$OUT/decisions.csv"; done

# JSON de iperf3 (perdidas reales y hora de inicio de cada rafaga)
for f in mininet/runtime/iperf_client_*.json; do sudo mv "$f" "$OUT/"; done
for f in mininet/runtime/iperf_server_*.json mininet/runtime/ping.log mininet/runtime/client.log; do
    sudo cp "$f" "$OUT/" || true
done

# Captura del canal de control y volcado de tablas de flujo, si existen
[ -f ctrl.pcap ] && sudo mv ctrl.pcap "$OUT/ctrl.pcap"
[ -f flujos_s1.txt ] && sudo mv flujos_s1.txt "$OUT/flujos_s1.txt"

sudo chown -R "$(id -u):$(id -g)" "$OUT"
echo "Guardado en $OUT:"; ls -1 "$OUT"
