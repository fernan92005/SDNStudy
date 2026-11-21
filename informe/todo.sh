#!/usr/bin/env bash
# Hace todo lo de GUIA_EJECUCION.md sin intervencion. Uso, desde cualquier carpeta de WSL:
#
#   sudo bash /ruta/a/SDNStudy/informe/todo.sh
#
# Toma los scripts de la carpeta informe/ donde esta este archivo y deja la
# salida en <carpeta de arriba>/salida_wsl. Para dejarla en otro sitio:
#   sudo WIN=/mnt/c/Users/<usuario>/Desktop/Trabajo bash .../informe/todo.sh
#
# Se puede relanzar: salta lo que ya este hecho (repo, entornos, modelo y cada
# ejecucion que ya tenga resultados/<nombre>/metrics.csv).
# Opcional: SOLO="congestion_predictive" sudo -E bash .../todo.sh   (una sola ejecucion)
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WIN="${WIN:-$(dirname "$SCRIPT_DIR")}"
REPO_URL="https://github.com/manjunathcshekar/predictive-sdn-routing.git"

[ "$EUID" -eq 0 ] || { echo "Lanzalo con sudo:  sudo bash \"$WIN/informe/todo.sh\""; exit 1; }
U="${SUDO_USER:?Lanzalo con sudo desde tu usuario, no como root directamente}"
UH="$(getent passwd "$U" | cut -d: -f6)"
REPO="$UH/predictive-sdn-routing"
LOG="$UH/todo_informe.log"
as_user() { sudo -u "$U" -H bash -c "cd '$REPO' 2>/dev/null; $*"; }
paso() { echo; echo "=== $(date +%H:%M:%S) $*"; }
fallo() { echo "ERROR: $*"; echo "Pasale a Claude el archivo $LOG"; exit 1; }
export PYTHONUNBUFFERED=1
exec > >(tee -a "$LOG") 2>&1

# ------------------------------------------------------------------ 1. paquetes
paso "1/7 Paquetes del sistema"
FALTAN=""
command -v mn        >/dev/null || FALTAN="$FALTAN mininet"
command -v ovs-vsctl >/dev/null || FALTAN="$FALTAN openvswitch-switch openvswitch-common"
command -v iperf3    >/dev/null || FALTAN="$FALTAN iperf3"
command -v tcpdump   >/dev/null || FALTAN="$FALTAN tcpdump"
command -v git       >/dev/null || FALTAN="$FALTAN git"
PYV="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
rm -rf /tmp/prueba_venv
python3 -m venv /tmp/prueba_venv >/dev/null 2>&1 || FALTAN="$FALTAN python3-venv python${PYV}-venv"
rm -rf /tmp/prueba_venv
if [ -n "$FALTAN" ]; then
    apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y $FALTAN net-tools python3-pip \
        || fallo "no se pudieron instalar:$FALTAN"
else
    echo "Todo instalado."
fi

# ------------------------------------------------------------------ 2. repo
paso "2/7 Repositorio en $REPO"
if [ ! -f "$REPO/mininet/ryu_app.py" ]; then
    if [ -e "$REPO" ]; then
        VIEJO="${REPO}_viejo_$(date +%Y%m%d_%H%M%S)"
        echo "La carpeta existe pero no tiene el repo completo. La muevo a $VIEJO (no borro nada)."
        mv "$REPO" "$VIEJO"
    fi
    sudo -u "$U" -H git clone "$REPO_URL" "$REPO" || fallo "git clone"
else
    echo "Repo encontrado."
fi
cd "$REPO" || fallo "no puedo entrar en $REPO"
sudo -u "$U" mkdir -p informe resultados
sudo -u "$U" cp -f "$SCRIPT_DIR"/*.py "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.md informe/ || fallo "copiar informe/"
as_user "python3 informe/aplicar_cambios.py" || fallo "aplicar_cambios.py"

# ------------------------------------------------------------------ 3. entornos
paso "3/7 Entornos de Python (la primera vez tarda: descarga TensorFlow)"
if ! as_user ".venv/bin/python -c 'import tensorflow, pandas, networkx, sklearn'" 2>/dev/null; then
    as_user "python3 -m venv --clear .venv && .venv/bin/pip install -q --upgrade pip && .venv/bin/pip install -q -r requirements.txt" \
        || fallo "crear .venv"
fi
if ! as_user ".venv-ryu/bin/python -c 'import os_ken, tensorflow, pandas'" 2>/dev/null; then
    as_user "python3 -m venv --clear .venv-ryu && .venv-ryu/bin/pip install -q --upgrade pip wheel os-ken && .venv-ryu/bin/pip install -q -r requirements.txt" \
        || fallo "crear .venv-ryu"
fi
echo "Entornos OK."

# ------------------------------------------------------------------ 4. modelo + simulacion
paso "4/7 Modelo LSTM y simulacion (incluye ECMP)"
if [ ! -f models/lstm.keras ] || [ ! -d results_trackA ]; then
    as_user "TF_CPP_MIN_LOG_LEVEL=3 .venv/bin/python scripts/run_all.py" || fallo "run_all.py"
    rm -rf results_trackA data_trackA
    as_user "cp -r results results_trackA && cp -r data data_trackA"
else
    echo "Ya hay modelo y results_trackA."
fi

# ------------------------------------------------------------------ 5. Mininet
paso "5/7 Open vSwitch"
modprobe openvswitch 2>/dev/null || true
/usr/share/openvswitch/scripts/ovs-ctl start --system-id=random >/dev/null 2>&1 || true
ovs-vsctl show >/dev/null || fallo "Open vSwitch no arranca"
echo "OVS OK."

CPID=""; TPID=""; XPIDS=""
limpiar() {
    touch /tmp/mn_stop
    [ -n "$XPIDS" ] && kill $XPIDS 2>/dev/null
    [ -n "$TPID" ] && { for _ in $(seq 30); do kill -0 "$TPID" 2>/dev/null || break; sleep 1; done; kill "$TPID" 2>/dev/null; }
    [ -n "$CPID" ] && kill "$CPID" 2>/dev/null
    sleep 2
    pkill -f "mininet/run_controller.py" 2>/dev/null
    pkill -f "informe/topo_auto.py" 2>/dev/null
    pkill -f "iperf3" 2>/dev/null
    mn -c >/dev/null 2>&1
    rm -f /tmp/mn_go /tmp/mn_started /tmp/mn_stop
    CPID=""; TPID=""; XPIDS=""
}
trap 'limpiar; exit 130' INT TERM

esperar_texto() {   # esperar_texto <fichero> <texto> <segundos>
    for _ in $(seq "$3"); do grep -q "$2" "$1" 2>/dev/null && return 0; sleep 1; done; return 1
}

ejecutar() {  # ejecutar NOMBRE MODE FORECASTER CLIENTE POL EXTRA
    local NAME="$1" MODE="$2" FC="$3" CLI="$4" POL="$5" EXTRA="$6"
    if [ -n "${SOLO:-}" ] && [ "$SOLO" != "$NAME" ]; then return 0; fi
    if [ -f "resultados/$NAME/metrics.csv" ]; then echo "$NAME ya hecho, lo salto."; return 0; fi
    paso "Ejecucion $NAME (10 min)  MODE=$MODE FORECASTER=$FC cliente=$CLI"
    limpiar
    rm -f mininet/runtime/decisions_*.csv mininet/runtime/active_path.json controller.log ctrl.pcap flujos_s1.txt
    [ -f informe/runtime_ref.json ] && cp informe/runtime_ref.json mininet/runtime.json

    MODE="$MODE" FORECASTER="$FC" TF_CPP_MIN_LOG_LEVEL=2 \
        .venv-ryu/bin/python mininet/run_controller.py --observe-links mininet/ryu_app.py > controller.log 2>&1 &
    CPID=$!
    if [ "$MODE" = predictive ]; then
        esperar_texto controller.log "Predictive mode ready" 180 || { tail -20 controller.log; limpiar; fallo "el controlador no cargo el LSTM"; }
    else
        sleep 8
    fi
    kill -0 "$CPID" 2>/dev/null || { tail -20 controller.log; fallo "el controlador se ha cerrado"; }

    python3 -u informe/topo_auto.py "$CLI" > "mininet_$NAME.log" 2>&1 &
    TPID=$!
    esperar_texto "mininet_$NAME.log" "AUTO: topologia arriba" 90 || { tail -20 "mininet_$NAME.log"; limpiar; fallo "Mininet no arranca"; }
    [ -f informe/runtime_ref.json ] || cp mininet/runtime.json informe/runtime_ref.json
    if ! esperar_texto controller.log "Installed IPv4 flows" 40; then
        limpiar
        [ -f "informe/.reintento_$NAME" ] && { tail -30 controller.log; fallo "el controlador no instala rutas (ver controller.log)"; }
        echo "El controlador no instalo rutas a la primera; repito la ejecucion con runtime.json guardado."
        touch "informe/.reintento_$NAME"; ejecutar "$@"; return
    fi
    rm -f "informe/.reintento_$NAME"
    touch /tmp/mn_go
    esperar_texto "mininet_$NAME.log" "AUTO: cliente" 120 || { tail -20 "mininet_$NAME.log"; limpiar; fallo "no se lanzo el trafico"; }
    if [ "$EXTRA" = flujos ] || [ "$EXTRA" = todo ]; then
        bash informe/volcar_flujos.sh 620 & XPIDS="$XPIDS $!"
    fi
    if [ "$EXTRA" = todo ]; then
        timeout 620 tcpdump -i lo -w ctrl.pcap tcp port 6633 >/dev/null 2>&1 & XPIDS="$XPIDS $!"
    fi
    echo "Recogiendo metricas 600 s (termina hacia las $(date -d '+10 min' +%H:%M))..."
    python3 -u mininet/collect_metrics.py --scenario "$CLI" --policy "$POL" \
        --duration 600 --interval 1.0 --out "data/mininet_${NAME}.csv" > "collector_$NAME.log" 2>&1 \
        || { tail -20 "collector_$NAME.log"; limpiar; fallo "colector"; }
    sleep 8
    limpiar
    bash informe/guardar_run.sh "$NAME" || fallo "guardar_run.sh"
    mv "mininet_$NAME.log" "collector_$NAME.log" "resultados/$NAME/" 2>/dev/null
    echo "  cambios de camino en el log: $(grep -c 'reroute \[' "resultados/$NAME/controller.log")"
}

paso "6/7 Las 5 ejecuciones en Mininet (unos 60 min)"
ejecutar normal_static          static     lstm        normal     static     no
ejecutar normal_predictive      predictive lstm        normal     predictive no
ejecutar congestion_static      static     lstm        congestion static     flujos
ejecutar congestion_predictive  predictive lstm        congestion predictive todo
ejecutar congestion_persistence predictive persistence congestion predictive no

# ------------------------------------------------------------------ 7. evaluacion y copia
paso "7/7 Evaluador del repo, metricas extra y copia a Windows"
chown -R "$U":"$U" resultados data results 2>/dev/null
for n in normal_static normal_predictive congestion_static congestion_predictive; do
    [ -f "data/mininet_$n.csv" ] || fallo "falta data/mininet_$n.csv"
    as_user "cp data/mininet_$n.csv data/$n.csv"
done
rm -f data/normal_ecmp.csv data/congestion_ecmp.csv
as_user "TF_CPP_MIN_LOG_LEVEL=3 .venv/bin/python scripts/05_evaluate.py" || fallo "05_evaluate.py"
as_user ".venv/bin/python informe/analisis_informe.py resultados --track-a results_trackA/summary.csv --track-a-decisions results_trackA/congestion_predictive_decisions.json" \
    || echo "AVISO: analisis_informe.py fallo (pasale el log a Claude)"
DEST="$WIN/salida_wsl"
mkdir -p "$DEST"
cp -r results resultados results_trackA "$DEST/"
cp "$LOG" "$DEST/"
paso "TERMINADO. Todo copiado en $DEST"
