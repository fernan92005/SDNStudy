#!/usr/bin/env python3
"""Levanta mininet/topology.py sin consola interactiva y lanza el trafico.

Lo usa informe/todo.sh. Sustituye la consola de Mininet (CLI) por una funcion
que hace lo mismo que se escribia a mano:

    pingall
    h2 bash mininet/traffic_iperf.sh server &
    h1 bash mininet/traffic_iperf.sh ping &
    h1 bash mininet/traffic_iperf.sh client <normal|congestion> &

Coordinacion con todo.sh mediante ficheros en /tmp:
    /tmp/mn_go      todo.sh lo crea cuando el controlador ya ha instalado rutas
    /tmp/mn_started lo crea este script cuando el cliente iperf3 ya esta lanzado
    /tmp/mn_stop    todo.sh lo crea al terminar el colector -> se para la red

Uso:  sudo python3 -u informe/topo_auto.py congestion
"""
import os
import runpy
import sys
import time
from pathlib import Path

CLIENT = sys.argv[1] if len(sys.argv) > 1 else "normal"
GO, STARTED, STOP = Path("/tmp/mn_go"), Path("/tmp/mn_started"), Path("/tmp/mn_stop")
MAX_S = 900


def esperar(f: Path, segundos: int) -> bool:
    for _ in range(segundos * 2):
        if f.exists():
            return True
        time.sleep(0.5)
    return False


class AutoCLI:
    def __init__(self, net, *args, **kwargs):
        print("AUTO: topologia arriba, esperando a que el controlador instale rutas", flush=True)
        if not esperar(GO, 120):
            print("AUTO: no llego la senal de inicio; paro la red", flush=True)
            return
        loss = net.pingAll()
        print(f"AUTO: pingall perdidas={loss}%", flush=True)
        h1, h2 = net.get("h1"), net.get("h2")
        h2.cmd("bash mininet/traffic_iperf.sh server > /dev/null 2>&1 &")
        time.sleep(2)
        h1.cmd("bash mininet/traffic_iperf.sh ping > /dev/null 2>&1 &")
        time.sleep(2)
        h1.cmd(f"bash mininet/traffic_iperf.sh client {CLIENT} > mininet/runtime/client_stdout.log 2>&1 &")
        STARTED.touch()
        print(f"AUTO: cliente '{CLIENT}' lanzado a las {time.strftime('%H:%M:%S')}", flush=True)
        esperar(STOP, MAX_S)
        print("AUTO: parando la red", flush=True)


for f in (GO, STARTED, STOP):
    f.unlink(missing_ok=True)

import mininet.cli  # noqa: E402

mininet.cli.CLI = AutoCLI
repo = Path(__file__).resolve().parents[1]
os.chdir(repo)
runpy.run_path(str(repo / "mininet" / "topology.py"), run_name="__main__")
