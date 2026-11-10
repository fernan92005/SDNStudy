# Track B — Mininet + Ryu reference implementation

This folder mirrors Track A's experimental loop on a real OpenFlow data
plane. The goal: re-run the same scenarios (`normal`, `congestion`) under
two policies (`static`, `predictive`) using real packets, real OVS
queueing, and Track A's trained LSTM and decision engine. The output CSVs
match Track A's schema bit-for-bit so `scripts/05_evaluate.py` consumes
them with no code changes.

## Files

| File | Role |
|---|---|
| [`topology.py`](topology.py) | Mininet topology mirroring `src/config.TopologyConfig`. Writes `runtime.json` (host PIDs, switch DPIDs, OF port map) for the other processes to consume. |
| [`ryu_app.py`](ryu_app.py) | OpenFlow controller (os-ken / Ryu-compatible). Two modes via `MODE=static\|predictive`; predictive mode runs the full LSTM-forecast → decision-engine → flow-rewrite loop every second. Writes `runtime/active_path.json` so the collector knows which path edges are currently load-bearing. |
| [`run_controller.py`](run_controller.py) | 30-line shim that boots `ryu_app.py` via `os_ken.base.app_manager.AppManager.run_apps`. Replaces `ryu-manager`, which doesn't ship with os-ken. |
| [`traffic_iperf.sh`](traffic_iperf.sh) | UDP iperf3 driver: `server` (on h2), `ping` (on h1), `client {normal,congestion}` (on h1). Burst windows match `src/config.py` exactly: `(120, 200)`, `(320, 410)`, `(500, 560)`. |
| [`collect_metrics.py`](collect_metrics.py) | Polls OVS port counters every second, reads ping RTTs + active-path JSON, emits the full 18-column Track A CSV. |
| `runtime.json` | Written by `topology.py`, read by everything else. Deleted on topology shutdown. |
| `runtime/active_path.json` | Written by `ryu_app.py`, read by `collect_metrics.py`. |
| `runtime/ping.log` | Written by `traffic_iperf.sh ping`, read by `collect_metrics.py`. |

## WSL2 setup

> Mininet on WSL2 needs a recent kernel with Open vSwitch available plus
> root privileges. systemd-on-WSL2 is the easy path; if you can't enable
> it, start OVS manually with `ovs-ctl start` as shown below.

### 1. Install system packages

```bash
sudo apt-get update
sudo apt-get install -y mininet openvswitch-switch openvswitch-common \
                        iperf3 net-tools python3-pip python3-venv
```

### 2. Start Open vSwitch (once per WSL session)

```bash
sudo /usr/share/openvswitch/scripts/ovs-ctl start --system-id=random
# Sanity:
sudo ovs-vsctl show
```

### 3. Controller venv (`.venv-ryu`) — uses os-ken, not stock Ryu

Stock Ryu 4.34 does not build on Python 3.12 (it imports the removed
`distutils` module and uses setuptools APIs dropped in setuptools 58).
We use **os-ken** instead -- the OpenStack Neutron-maintained Ryu fork
with the same module layout and event API. The launcher is
`mininet/run_controller.py`, a 30-line shim around
`os_ken.base.app_manager.AppManager.run_apps`.

```bash
cd /home/mcshekar/workbench/whitepaper
python3 -m venv .venv-ryu
.venv-ryu/bin/pip install --upgrade pip wheel
.venv-ryu/bin/pip install os-ken
# Track A's modules (used by predictive mode) need TF, pandas, joblib, ...
.venv-ryu/bin/pip install -r requirements.txt
```

### 4. Verify

```bash
mn --version                                                              # 2.3.x
ovs-vsctl --version                                                       # 3.x
iperf3 --version                                                          # 3.x
.venv-ryu/bin/python -c 'import os_ken; print(os_ken.__version__)'        # 4.2.x
.venv-ryu/bin/python mininet/run_controller.py --help                     # arg parser works
```

## Running an experiment

Each experiment needs **four** processes, one per terminal. The
recommended order (some can wait until their predecessor is up):

### Terminal 1 — Controller

```bash
cd /home/mcshekar/workbench/whitepaper
# Pick the mode for this run:
MODE=static     .venv-ryu/bin/python mininet/run_controller.py --observe-links mininet/ryu_app.py
# or:
MODE=predictive .venv-ryu/bin/python mininet/run_controller.py --observe-links mininet/ryu_app.py
```

Wait for `Topology ready: switches=[...]` before starting Mininet. In
predictive mode the first log line you should see is
`Predictive mode ready: forecaster window=20 horizon=5` -- that's the
LSTM weights loading. Loading TensorFlow takes ~10 s on a cold cache.

### Terminal 2 — Mininet topology

Use the **system** Python here, not Track A's `.venv`. The `mininet`
package is apt-installed into `/usr/lib/python3/dist-packages/mininet`,
which isn't on any of the project venvs' `sys.path`. (`topology.py`
only needs `mininet.*` and stdlib, no Track A modules, so system
Python is correct.)

```bash
cd /home/mcshekar/workbench/whitepaper
sudo python3 mininet/topology.py
# After it prints "Wrote .../runtime.json" and "Topology up. Try: pingall",
# you'll be at the Mininet CLI:
mininet> pingall
# Expected: h1 -> h2, h2 -> h1, 0% loss.
```

`topology.py` writes `mininet/runtime.json` on `net.start()` so the
controller and collector can resolve OF port numbers.

### Terminal 3 — Traffic + ping (run inside Mininet)

From the Mininet CLI:

```text
mininet> h2 bash mininet/traffic_iperf.sh server &
mininet> h1 bash mininet/traffic_iperf.sh ping &
mininet> h1 bash mininet/traffic_iperf.sh client congestion    # or 'normal'
```

The `server` invocation starts iperf3 servers on ports 5201 (baseline)
and 5202 (burst). The `ping` invocation logs 1 Hz RTTs to
`mininet/runtime/ping.log` for the latency column. The `client` call
drives the offered-load profile.

### Terminal 4 — Metrics collector

`collect_metrics.py` is pure stdlib + `subprocess` calls to `ovs-ofctl`,
so system `python3` works fine here too. (Track A's `.venv` also works
if you prefer it -- the collector doesn't `import src.*`.)

**Must be run with `sudo`** -- `ovs-ofctl` needs root to read the OVS
control socket. Without `sudo` the collector now refuses to start (it
used to fail silently and write zero-throughput CSVs).

```bash
cd /home/mcshekar/workbench/whitepaper
sudo python3 mininet/collect_metrics.py \
    --scenario congestion --policy static \
    --duration 600 --interval 1.0 \
    --out data/mininet_congestion_static.csv
```

After 600 seconds you have a CSV with all 18 canonical columns:
`tick, scenario, policy, link, src, dst, offered_load_mbps,
carried_load_mbps, capacity_mbps, utilization, latency_ms,
packet_loss_pct, throughput_mbps, path_index, is_on_active_path,
effective_latency_ms, effective_throughput_mbps, effective_packet_loss_pct`.

## The four runs needed for the paper

Section 7 step 9 of `HANDOFF_TRACK_B.md` calls for four CSVs. Repeat the
terminal-1..4 dance once per row of:

| Scenario | MODE | --scenario | --policy | --out |
|---|---|---|---|---|
| normal     | static     | normal     | static     | `data/mininet_normal_static.csv` |
| normal     | predictive | normal     | predictive | `data/mininet_normal_predictive.csv` |
| congestion | static     | congestion | static     | `data/mininet_congestion_static.csv` |
| congestion | predictive | congestion | predictive | `data/mininet_congestion_predictive.csv` |

Then symlink (or copy) the four CSVs into `data/` with the names the
Track A evaluator expects, and run it:

```bash
cp data/mininet_normal_static.csv     data/normal_static.csv
cp data/mininet_normal_predictive.csv data/normal_predictive.csv
cp data/mininet_congestion_static.csv data/congestion_static.csv
cp data/mininet_congestion_predictive.csv data/congestion_predictive.csv
.venv/bin/python scripts/05_evaluate.py
```

(Track A's static + ECMP + predictive CSVs live in the same folder; back
them up first if you want both side-by-side reports.)

## Troubleshooting

- **Controller import errors about `ryu.*`.** That's stale code from
  before the os-ken migration. The current `ryu_app.py` imports
  `os_ken.*`; if you see Ryu imports, you've checked out an older
  revision.
- **`pingall` shows X loss.** OVS isn't running, or the controller
  didn't install ARP/L2-learning flows. Check `ovs-vsctl show` and the
  controller log for `Topology ready`.
- **CSV is all-zeros.** Either (a) you ran the collector without
  `sudo`, so `ovs-ofctl` was rejected by the OVS control socket -- the
  current build prints `ovs-ofctl dump-ports sN failed (exit 1)` and
  exits with a hint, so this should be loud now; or (b) `runtime.json`
  wasn't written, so port lookups all default to zero -- make sure the
  Mininet topology started cleanly and the file exists at
  `mininet/runtime.json`; or (c) the collector window simply didn't
  overlap with `iperf3`. Run `iperf3` first (backgrounded with `&`) and
  start the collector within a few seconds, both for the same wall-clock
  window.
- **Predictive controller never reroutes.** The first 20 ticks have no
  history; reroutes only happen once the rolling window is full and the
  forecast peaks ≥ 0.75. During a `congestion` run you should see
  `[tick N] reroute ... -> ...` lines in the controller log within ~15
  s of the first burst at tick 120.
- **`tx_dropped` always zero on `s1-s2`.** Confirm `traffic_iperf.sh
  client congestion` is using UDP (it is, by default). TCP would
  back off rather than drop, hiding loss behind retransmits.
