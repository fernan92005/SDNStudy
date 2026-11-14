#!/usr/bin/env bash
# Vuelca la tabla de flujos de s1 una vez por segundo con la hora, para poder
# localizar despues el "antes" y el "despues" de cada cambio de camino.
# Uso (terminal 4, con sudo):  sudo bash informe/volcar_flujos.sh 600
DUR="${1:-600}"
cd "$(dirname "$0")/.." || exit 1
: > flujos_s1.txt
for ((i = 0; i < DUR; i++)); do
    {
        echo "=== $(date +%s.%N | cut -c1-14)  $(date +%H:%M:%S)"
        ovs-ofctl -O OpenFlow13 dump-flows s1 | grep 'priority=100' || true
    } >> flujos_s1.txt
    sleep 1
done
