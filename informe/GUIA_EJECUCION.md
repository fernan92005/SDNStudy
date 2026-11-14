# Guía para sacar los resultados (WSL)

Todo se ejecuta desde la carpeta del repo en WSL (`~/predictive-sdn-routing`).
Abre 4 terminales de WSL y en todas haz primero `cd ~/predictive-sdn-routing`.

## 0. Preparación (una vez)

```bash
# Copiar esta carpeta al repo
cp -r "/mnt/c/Users/forza/Desktop/UNIVERSIDAD/26-27/Redes/Trabajo/informe" ~/predictive-sdn-routing/
cd ~/predictive-sdn-routing

# Parche: el controlador guarda la predicción de cada tick y la escribe en el log,
# admite FORECASTER=persistence, y el colector añade la hora (ts_epoch) a cada fila
git apply informe/informe.patch
git diff --stat           # deben salir ryu_app.py y collect_metrics.py

# Si aún no tenéis el modelo entrenado y la simulación (incluye ECMP):
ls models/lstm.keras || .venv/bin/python scripts/run_all.py
# Guardar la simulación antes de que el evaluador la sobrescriba
cp -r results results_trackA
cp -r data data_trackA

sudo /usr/share/openvswitch/scripts/ovs-ctl start --system-id=random
sudo apt-get install -y tcpdump      # para medir el canal de control
mkdir -p resultados
```

## 1. Las ejecuciones

Cada ejecución dura 10 minutos. Se repite lo mismo para cada fila de la tabla;
solo cambian `MODE`, el escenario del cliente y el nombre.

| # | Nombre (`NOMBRE`) | MODE | Cliente | Notas |
|---|---|---|---|---|
| 1 | `normal_static` | static | `client normal` | |
| 2 | `normal_predictive` | predictive | `client normal` | |
| 3 | `congestion_static` | static | `client congestion` | capturar flujos (siempre camino principal) |
| 4 | `congestion_predictive` | predictive | `client congestion` | **capturas del log + flujos + tcpdump** |
| 5 | `congestion_persistence` (opcional) | predictive + `FORECASTER=persistence` | `client congestion` | referencia trivial frente al LSTM |

### Terminal 1: controlador (con log a fichero)

```bash
MODE=predictive .venv-ryu/bin/python mininet/run_controller.py \
    --observe-links mininet/ryu_app.py 2>&1 | tee controller.log
# Para la 5.ª ejecución:
# MODE=predictive FORECASTER=persistence .venv-ryu/bin/python mininet/run_controller.py \
#     --observe-links mininet/ryu_app.py 2>&1 | tee controller.log
```
Espera a `Predictive mode ready` (en static no sale) y luego a `Installed IPv4 flows for path ['h1', 's1', 's2', 'h2']`.

### Terminal 2: Mininet

```bash
sudo python3 mininet/topology.py
mininet> pingall
mininet> h2 bash mininet/traffic_iperf.sh server &
mininet> h1 bash mininet/traffic_iperf.sh ping &
mininet> h1 bash mininet/traffic_iperf.sh client congestion &     # o: client normal &
```

### Terminal 3: colector (lánzalo en cuanto lances el cliente)

```bash
NOMBRE=congestion_predictive    # cambia en cada ejecución
POL=predictive                  # static o predictive (en la 5.ª también predictive)
ESC=congestion                  # normal o congestion
sudo python3 mininet/collect_metrics.py --scenario $ESC --policy $POL \
    --duration 600 --interval 1.0 --out data/mininet_${NOMBRE}.csv
```

### Terminal 4: tablas de flujo y canal de control (ejecuciones 3 y 4)

```bash
# Volcado de la tabla de s1 cada segundo, con la hora (para elegir el antes/después)
sudo bash informe/volcar_flujos.sh 600 &
# Solo en la ejecución 4: tráfico controlador <-> switches durante 10 min
sudo timeout 600 tcpdump -i lo -w ctrl.pcap tcp port 6633
```

### Capturas para el informe (ejecución 4)

En la terminal 1 verás líneas así cuando la predicción pasa el umbral:

```
[tick 118] util s1-s2=0.31 | prediccion t+1..t+5=[0.62 0.71 0.78 0.80 0.81] pico=0.81 -> reroute_traffic (forecast peak 0.81 >= threshold 0.75)
[tick 118] reroute ['h1','s1','s2','h2'] -> ['h1','s1','s3','s2','h2'] (total reroutes=1)
Installed IPv4 flows for path ['h1', 's1', 's3', 's2', 'h2']
...
[tick 124] ... -> keep_default_route (forecast peak 0.12 cleared hysteresis band)
```

Haz **tres capturas** de la terminal 1: (a) la predicción que dispara el desvío, (b) el cambio de camino, (c) la vuelta al principal.

Para las tablas de flujo, en un hueco de la terminal 4:

```bash
sudo ovs-ofctl -O OpenFlow13 dump-flows s1 | grep priority=100
```

- **Antes** (camino principal, p. ej. en t≈100 s): `ipv4_dst=10.0.0.2 actions=output:"s1-eth2"` (hacia s2).
- **Durante** la ráfaga, tras el desvío: `actions=output:"s1-eth3"` (hacia s3).
- **Después**, al volver: de nuevo `s1-eth2`. Fíjate en `duration=`: se reinicia en cada cambio porque el controlador borra las reglas y las vuelve a crear.

Si se te escapa el momento, `flujos_s1.txt` tiene la tabla de cada segundo con la hora.

### Al terminar cada ejecución

```bash
# Terminal 2: mininet> exit      Terminal 1: Ctrl+C
sudo mn -c
bash informe/guardar_run.sh $NOMBRE      # mueve CSV, log, decisiones, iperf y capturas a resultados/$NOMBRE
```

## 2. Evaluador del repo y métricas extra

```bash
cp data/mininet_normal_static.csv          data/normal_static.csv
cp data/mininet_normal_predictive.csv      data/normal_predictive.csv
cp data/mininet_congestion_static.csv      data/congestion_static.csv
cp data/mininet_congestion_predictive.csv  data/congestion_predictive.csv
rm -f data/normal_ecmp.csv data/congestion_ecmp.csv   # ECMP solo existe en la simulación
.venv/bin/python scripts/05_evaluate.py               # results/summary.csv, deltas.csv, figures/

.venv/bin/python informe/analisis_informe.py resultados \
    --track-a results_trackA/summary.csv \
    --track-a-decisions results_trackA/congestion_predictive_decisions.json
```

El segundo script escribe `resultados/tabla_extra.csv` y `tabla_extra.tex` (cambios de ruta,
pérdidas según iperf3, p50/p95/p99, latencia solo durante las ráfagas, antelación del aviso en
cada ráfaga, kbit/s del canal de control) y `resultados/linea_temporal_*.png`
(utilización, predicción, umbrales y camino activo).

## 2b. LSTM frente a persistencia en el simulador (2 min)

```bash
.venv/bin/python informe/persistencia_sim.py
```

Escribe `informe_persistencia/` con tres tablas: error de predicción (LSTM, persistencia y
media móvil), la congestión como evento (precisión, recall, F1 y antelación) y el lazo cerrado
con cada predictor. Copia también esa carpeta en el paso 3.

## 3. Qué pasarme

Copia a Windows `results/` y `resultados/` enteras, y las capturas:

```bash
DEST="/mnt/c/Users/forza/Desktop/UNIVERSIDAD/26-27/Redes/Trabajo/salida_wsl"
mkdir -p "$DEST" && cp -r results resultados results_trackA informe_persistencia "$DEST/"
```
