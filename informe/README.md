# Scripts del informe (Resultados y Análisis crítico)

Cambios al código original y herramientas que usamos para sacar los resultados de las
secciones 4 y 5 del informe.

## Cambios en el repositorio

- `mininet/ryu_app.py`
  - El controlador escribe una fila por ciclo en `mininet/runtime/decisions_<MODE>_<FORECASTER>.csv`, con la utilización medida, la predicción t+1..t+5, el pico, la acción y el motivo.
  - Añade una línea al log cuando la predicción pasa el umbral o cambia la acción.
  - Admite `FORECASTER=persistence`, que sustituye el LSTM por la referencia trivial (repetir la última utilización). La regla de decisión no cambia.
- `mininet/collect_metrics.py`: añade la columna `ts_epoch` para poder alinear el CSV con el log del controlador y con iperf3.

`informe/aplicar_cambios.py` hace estos mismos cambios sobre una copia limpia del repositorio original, y se puede ejecutar más de una vez.

## Scripts

| Archivo | Para qué |
|---|---|
| `todo.sh` | Hace todo de una vez en WSL: paquetes, entornos, `run_all.py`, las 5 ejecuciones de Mininet, el evaluador y el análisis. Se lanza con `sudo bash informe/todo.sh`. Ojo: la ruta `WIN` del principio apunta a la carpeta de Víctor. |
| `topo_auto.py` | Levanta `mininet/topology.py` sin la consola interactiva y lanza el tráfico (lo usa `todo.sh`). |
| `guardar_run.sh` | Guarda en `resultados/<nombre>/` el CSV, el log, las decisiones, los JSON de iperf3 y las capturas de cada ejecución. |
| `volcar_flujos.sh` | Vuelca cada segundo la tabla de flujos de s1, con la hora. |
| `analisis_informe.py` | Calcula lo que el evaluador no saca: cambios de ruta, pérdidas según iperf3, p50/p95/p99, latencia en ráfagas, antelación del aviso y carga del canal de control. También dibuja la línea temporal. |
| `persistencia_sim.py` | Compara en el simulador el LSTM con la persistencia y con una media móvil: error de predicción, la congestión como evento (precisión, recall, F1, antelación) y el lazo cerrado. |
| `canal_control_tipos.py` | Cuenta los mensajes OpenFlow de una captura `ctrl.pcap` por tipo: número, bytes y frecuencia. |
| `canal_control_packet_in.py` | Clasifica los `PACKET_IN` de la captura según lo que transportan (IPv6, ARP o UDP de datos). |
| `GUIA_EJECUCION.md` | Pasos manuales, por si no se usa `todo.sh`. |

## Las 5 ejecuciones

| Nombre | MODE | FORECASTER | Escenario |
|---|---|---|---|
| normal_static | static | – | normal |
| normal_predictive | predictive | lstm | normal |
| congestion_static | static | – | congestion |
| congestion_predictive | predictive | lstm | congestion |
| congestion_persistence | predictive | persistence | congestion |
