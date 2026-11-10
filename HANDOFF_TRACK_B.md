# Hand-off — Track B (Mininet + Ryu) for the 6G AI-Driven Predictive Routing white paper

**You (the receiving agent) are picking up the second half of a 2-track project.**
Track A (pure-Python fluid simulator + LSTM + decision engine + 3-way evaluator) is **finished, validated, and frozen.** Your job is Track B: run the same prediction-and-routing loop on real packets through a Mininet topology controlled by a Ryu/OpenFlow application, and show the headline trends from Track A reproduce on a real (virtualised) OpenFlow data plane.

Do not modify anything under `src/`, `scripts/`, `data/`, `models/`, `results/`, or `docs/` unless explicitly instructed by the user. Treat Track A as a frozen library you call into.

---

## 0. TL;DR for the receiving agent

- **Repo root:** `/home/mcshekar/workbench/whitepaper`
- **OS:** WSL2 Ubuntu (kernel `6.6.87.2-microsoft-standard-WSL2`)
- **Python venv:** `.venv/bin/python` (TensorFlow, NetworkX, pandas, etc. already installed)
- **Read first, in order:** [`PROGRESS.md`](PROGRESS.md), [`docs/architecture.md`](docs/architecture.md), [`docs/methodology.md`](docs/methodology.md), [`mininet/README.md`](mininet/README.md)
- **Track B starter files (skeletons):** [`mininet/topology.py`](mininet/topology.py), [`mininet/ryu_app.py`](mininet/ryu_app.py), [`mininet/traffic_iperf.sh`](mininet/traffic_iperf.sh), [`mininet/collect_metrics.py`](mininet/collect_metrics.py)
- **Acceptance criterion:** produce a Mininet CSV that the existing Track A evaluator scores with the same qualitative ordering as Track A (predictive ≪ static on congestion latency/loss; ECMP ≤ predictive on this topology because the alternate path is over-provisioned).

---

## 1. What is the project (one paragraph)

We are submitting to **White Paper 2026 — "Intelligent Network: AI-Driven Connectivity for the Next Generation."** The title is *AI-Driven Predictive Network Traffic Management for 6G Using Edge Intelligence*. The core idea: replace **reactive** congestion handling (wait for queues to build → drop → backoff) with a **forecast-then-prevent** loop running on the edge — an LSTM predicts per-link utilization a few seconds ahead, a rule-based decision engine turns that into a `KEEP / REROUTE` decision, and an SDN-style routing layer applies it. Architecturally it is the structural equivalent of an **O-RAN Near-RT RIC xApp** (control plane, observing the data plane, acting at sub-second latencies). Track A is the math/ML proof; Track B is the OpenFlow proof that the same control loop works on real packets.

---

## 2. What's already built (Track A) — DO NOT MODIFY

### Code

| Module | Purpose |
|---|---|
| [`src/config.py`](src/config.py) | Central config: topology, traffic, LSTM hparams, decision thresholds |
| [`src/simulation/topology.py`](src/simulation/topology.py) | Builds the NetworkX graph |
| [`src/simulation/traffic_generator.py`](src/simulation/traffic_generator.py) | Normal / congestion / training traffic scenarios |
| [`src/simulation/simulator.py`](src/simulation/simulator.py) | Discrete-time fluid model. **Now supports weighted multi-path policies** (e.g. ECMP) via a `List[Tuple[List[str], float]]` policy return type. Single-path policies (static, predictive) can still return a plain `List[str]`. |
| [`src/data/collector.py`](src/data/collector.py) | Canonical CSV schema (see §4) |
| [`src/data/preprocessor.py`](src/data/preprocessor.py) | Sliding-window + MinMax scaling |
| [`src/ml/lstm_model.py`](src/ml/lstm_model.py), [`src/ml/train.py`](src/ml/train.py), [`src/ml/predict.py`](src/ml/predict.py) | LSTM forecaster (window 20, horizon 5, 4 features per link). **Trained weights live in `models/lstm.keras` plus the two `*.pkl` scalers and `metadata.json`** |
| [`src/decision/engine.py`](src/decision/engine.py) | Rule + hysteresis: reroute if predicted peak ≥ 0.75, return if < 0.65 for ≥ 5 ticks |
| [`src/sdn/router_static.py`](src/sdn/router_static.py) | WITHOUT-AI baseline #1 — shortest path, fixed |
| [`src/sdn/router_ecmp.py`](src/sdn/router_ecmp.py) | WITHOUT-AI baseline #2 — 50/50 split across both simple paths (industry default) |
| [`src/sdn/router_predictive.py`](src/sdn/router_predictive.py) | WITH-AI policy |
| [`src/evaluation/metrics.py`](src/evaluation/metrics.py) | Per-experiment scalar summary. **Honours the new `effective_*` per-tick columns when present**, so multi-path policies are reported as a single end-to-end flow rather than worst-link-bottleneck. |
| [`src/evaluation/compare.py`](src/evaluation/compare.py) | N-way deltas vs the static baseline |
| [`src/visualization/plots.py`](src/visualization/plots.py) | Time-series + bar plots; auto-adapts to any number of policies |
| [`scripts/01..05_*.py`](scripts/) + [`scripts/run_all.py`](scripts/run_all.py) | End-to-end pipeline (`run_all.py` runs everything in order) |

### Already-trained model

`models/lstm.keras` + `models/feature_scaler.pkl` + `models/target_scaler.pkl` + `models/metadata.json`. **Reuse it.** Track B should call `from src.ml.predict import load_forecaster` and let `load_forecaster()` pick up the persisted artefacts — do not retrain.

### Headline Track A results (use to validate Track B trends)

Congestion scenario, 600 ticks:

| metric | Static | ECMP | Predictive |
|---|---:|---:|---:|
| avg latency (ms) | 38.40 | **5.87** | 15.57 |
| p95 latency (ms) | 100.23 | **8.33** | 100.22 |
| avg packet loss (%) | 5.65 | **0.00** | 0.91 |
| congestion ticks ≥ 0.9 util (%) | 35.33 | **0.00** | 6.50 |
| avg throughput (Mbps) | 52.15 | **59.12** | 58.00 |

Normal scenario: all three policies are essentially equivalent (predictive provably takes **0** reroutes; ECMP pays a small constant ~2 ms latency premium for using both paths).

**Honest finding the paper acknowledges:** on this particular topology ECMP also beats predictive, because the alternate path's 180 Mbps capacity is wide enough that a static 50/50 split absorbs the entire 120 Mbps burst. Predictive trails ECMP because of a brief detection-lag tax at burst start. Your Track B results should reproduce this ordering (or a defensible variant once Mininet's per-flow ECMP hashing is taken into account).

---

## 3. Your job (Track B) — specifically this

Produce four artefacts on a real Mininet/OVS data plane controlled by Ryu:

1. A **Mininet topology** that exactly mirrors Track A's `src/config.py` capacities and latencies (see §5 — there is currently a 80 vs 180 Mbps discrepancy you must fix).
2. A **Ryu app** with two modes (`MODE=static` and `MODE=predictive`) that:
   - In `static` mode, installs shortest-path flows once and leaves them.
   - In `predictive` mode, polls OFPPortStats every second, feeds them into Track A's `LoadedForecaster`, asks Track A's `DecisionEngine` what to do, and reinstalls flows along the chosen path. (Optional: implement an `ecmp` mode using OFPGroupMod `select` groups for completeness.)
3. A **traffic driver** (`mininet/traffic_iperf.sh`) that reproduces the same offered-load shape as Track A's `congestion_scenario` (baseline ~25 Mbps with three 120 Mbps bursts inside the 600 s window).
4. A **metrics collector** that polls OVS port counters and writes a CSV matching Track A's canonical schema (see §4), so Track A's evaluator (`scripts/05_evaluate.py`) scores it with no code changes.

Final deliverable: a `results/track_b_summary.csv` (or equivalent) plus a short prose section in `docs/methodology.md` (or a new `docs/track_b.md`) reporting "Mininet results qualitatively match Track A" with the same metric table.

---

## 4. CSV schema contract (critical — do not deviate)

Track A's canonical schema is defined as `COLUMNS` in [`src/data/collector.py`](src/data/collector.py):

```
tick, scenario, policy, link, src, dst,
offered_load_mbps, carried_load_mbps, capacity_mbps, utilization,
latency_ms, packet_loss_pct, throughput_mbps,
path_index, is_on_active_path,
effective_latency_ms, effective_throughput_mbps, effective_packet_loss_pct
```

Notes on the new `effective_*` columns (added when ECMP was implemented):

- They are **per-tick** flow-level values, replicated on every row of that tick. Required for honest reporting of multi-path policies; for single-path policies they reduce to the natural per-path values.
- Single-path Mininet runs (static, predictive) can set them to the path-level sum/min/max — that matches Track A's semantics.
- If you implement a real OVS ECMP mode, compute these from the per-flow split exactly as `src/simulation/simulator.py` does.

The currently-shipped `mininet/collect_metrics.py` is missing `is_on_active_path` and all three `effective_*` columns. You must add them before the Track A evaluator will accept the output.

---

## 5. Starter files on disk — what's wrong with them

These files exist but are scaffolds, not finished products. Each has an intentional wiring point that needs your work.

### `mininet/topology.py`

- Brings up `h1, h2, s1, s2, s3` with TC-shaped links.
- **BUG to fix:** lines 41–42 set `s1↔s3` and `s3↔s2` to `bw=80` Mbps and `delay="3ms"`, but Track A uses **180 Mbps** for those links (see `src/config.py` → `TopologyConfig.links`). Change to `bw=180` so Track B and Track A are directly comparable.
- Uses a `RemoteController` on `127.0.0.1:6633`, which is the port Ryu binds to by default.

### `mininet/ryu_app.py`

- Skeleton of `PredictiveRouter(RyuApp)` with topology discovery, `_packet_in`, and a `_monitor()` hub that ticks every `STATS_INTERVAL` seconds.
- **The `_maybe_reroute()` hook is intentionally empty** (`# Intentionally left as a wiring point.`). The docstring of that method enumerates the four steps you must implement:
  1. Sweep `OFPPortStatsRequest` across switches, compute per-link utilisation deltas.
  2. Feed each link's recent window into `src.ml.predict.LoadedForecaster.predict_link(df)`.
  3. Call `src.decision.engine.DecisionEngine.step(forecasts)` to get a `KEEP / REROUTE`.
  4. Install `OFPFlowMod` along the chosen path (primary `h1-s1-s2-h2` or alternate `h1-s1-s3-s2-h2`).
- The `MODE` environment variable selects `static` vs `predictive`. The forecaster is only loaded when `MODE=predictive`.

### `mininet/traffic_iperf.sh`

- Existing script drives `iperf3` between `h1` and `h2`. It currently runs a flat profile.
- **Extend it** to reproduce Track A's congestion shape: ~25 Mbps baseline for the whole 600 s window, plus three burst windows of ~120 Mbps at roughly ticks 120–200, 320–410, 500–560. Use `iperf3 -b <rate>M -t <duration>` plus `sleep`s or run multiple sub-flows.

### `mininet/collect_metrics.py`

- Polls OVS port counters with `ovs-ofctl dump-ports` and writes CSV.
- **Missing columns** (see §4): `is_on_active_path`, `effective_latency_ms`, `effective_throughput_mbps`, `effective_packet_loss_pct`. For single-path Mininet runs, set `is_on_active_path = (link in chosen_path)` and `effective_* = path-aggregate` (the simulator's semantics).
- **`latency_ms` and `packet_loss_pct` are currently hardcoded to 0.** Either (a) leave them at 0 and document that Track B reports utilisation only, or (b) measure latency via `ping`/`iperf3 --get-server-output` parallel runs and parse the loss field. Option (b) is preferred because the headline metric in the paper is latency.

---

## 6. WSL2 environment notes

- This is **WSL2**, not native Linux. Mininet works but with caveats:
  - `sudo` is required for `mn`, `ovs-vsctl`, `ovs-ofctl`, `ryu-manager` because they manipulate network namespaces and kernel datapaths.
  - WSL2 historically needs `sudo service openvswitch-switch start` (or `sudo /etc/init.d/openvswitch-switch start`) before Mininet can talk to OVS. If `ovs-vsctl show` errors out, that's why.
  - No GUI / X server is required — everything is CLI.
- Use the project's `.venv` for any Python that imports Track A modules (`/home/mcshekar/workbench/whitepaper/.venv/bin/python`). System Python won't have TensorFlow.
- Ryu's own venv requirement: Ryu pins old eventlet versions that may conflict with the project venv. **Recommendation:** install Ryu in a separate venv (`.venv-ryu`) and have the Ryu app call into Track A by adding the project root to `PYTHONPATH`. The current `mininet/README.md` describes this.

---

## 7. Step-by-step task list

Work through this in order. Each step has a concrete "done when" gate.

1. **Install prerequisites**
   - `sudo apt-get install -y mininet openvswitch-switch iperf3`
   - `pip install --user ryu` (in a fresh venv if it conflicts)
   - Done when `mn --version`, `ovs-vsctl --version`, `ryu-manager --version`, `iperf3 --version` all print.
2. **Bring up the topology bare-bones**
   - `sudo .venv/bin/python mininet/topology.py`, then `pingall` in the Mininet CLI.
   - Done when `pingall` shows 0 % loss between `h1` and `h2`.
3. **Fix the 80 → 180 Mbps discrepancy** in `mininet/topology.py` and re-run `pingall`.
4. **Start Ryu in static mode** in another terminal:
   - `MODE=static ryu-manager --observe-links mininet/ryu_app.py`
   - Done when Ryu logs `Topology ready: switches=[1, 2, 3]`.
5. **Drive baseline traffic** with `mininet/traffic_iperf.sh` and confirm `iperf3` reports throughput near the expected ~25 Mbps baseline.
6. **Run the collector**: `python3 mininet/collect_metrics.py --duration 600 --interval 1.0 --out data/mininet_static_congestion.csv`. Verify the CSV has all required columns from §4.
7. **Implement the burst profile** in `traffic_iperf.sh` (three 120 Mbps windows) and re-run; confirm collector CSV shows the bursts as utilisation spikes on `s1-s2`.
8. **Implement `_maybe_reroute`** in `ryu_app.py` — the four steps documented in its docstring. Test under `MODE=predictive`.
9. **Capture all four Mininet runs** (`{normal, congestion} × {static, predictive}` — ECMP can be deferred). Output: `data/mininet_*.csv`.
10. **Score with Track A's evaluator**: copy the Mininet CSVs into `data/`, then run `python scripts/05_evaluate.py`. It should accept the new files without code changes.
11. **Write `docs/track_b.md`** with the resulting metric table and a one-paragraph "trends match" statement.

---

## 8. Acceptance criteria

- Mininet CSVs pass through `scripts/05_evaluate.py` unmodified.
- For the congestion scenario, the **ordering** of `avg_latency_ms` is `static > predictive` (matching Track A). Absolute numbers will differ from Track A because Track B has real packets, real TCP, and real queueing — that is expected and good.
- The Ryu predictive controller fires a non-zero number of `OFPFlowMod` rewrites during congestion bursts and zero (or near-zero) during the normal scenario.
- Pipeline reproducible from `mininet/README.md` instructions alone.

---

## 9. Locked-in decisions (do NOT re-debate)

These are settled. Save the user from re-litigating them.

- Title is fixed: *AI-Driven Predictive Network Traffic Management for 6G Using Edge Intelligence*.
- Decision engine is **rule-based with hysteresis.** Reinforcement learning is explicitly deferred to future work for this paper.
- Topology is the 3-switch multipath in `src/config.py`. Do not change capacities, latencies, or the host/switch names; Track B mirrors Track A.
- LSTM hyperparameters and trained weights are frozen. Reuse `models/lstm.keras`. Do not retrain.
- ECMP is already implemented in Track A as the second WITHOUT-AI baseline. Implementing it under OVS in Track B is **optional / nice-to-have**, not required for the first submission.
- O-RAN framing (Near-RT RIC xApp) is the architectural positioning. Mininet/Ryu is the proof-of-concept, not the production deployment.

---

## 10. Open questions — escalate to the user, do not decide unilaterally

- Whether to measure latency in Track B via parallel `ping` (cheap, RTT-only) or via per-flow `iperf3 --get-server-output` parsing (richer but fiddlier).
- Whether to implement OVS-level ECMP via OFPGroupMod `select` groups, or to skip it for the first Track B pass.
- Whether to package the Ryu app as a containerised xApp for the O-RAN SC sandbox (Phase 3 stretch — not for the contest submission).
- Whether to use TC's `netem` to inject artificial loss/delay on `s1-s2` to amplify the predictive policy's win (research-honest if disclosed; risky otherwise).

---

## 11. Commands cheat-sheet

```bash
# Project venv (TF, NetworkX, pandas)
cd /home/mcshekar/workbench/whitepaper
source .venv/bin/activate     # or use .venv/bin/python directly

# Re-run Track A end-to-end (sanity check)
export MPLCONFIGDIR=/tmp/mpl-whitepaper
.venv/bin/python scripts/run_all.py

# Track B — one shell per process
sudo service openvswitch-switch start
sudo .venv/bin/python mininet/topology.py        # Mininet CLI
MODE=static  ryu-manager --observe-links mininet/ryu_app.py   # Ryu (separate terminal)
bash mininet/traffic_iperf.sh                                  # Traffic (separate terminal)
.venv/bin/python mininet/collect_metrics.py \
    --duration 600 --interval 1.0 \
    --out data/mininet_static_congestion.csv                   # Collector

# Score Mininet CSVs with Track A evaluator
.venv/bin/python scripts/05_evaluate.py
```

---

## 12. Where the truth lives

- **What we're building and why:** [`docs/abstract.md`](docs/abstract.md)
- **System architecture diagram + module map:** [`docs/architecture.md`](docs/architecture.md)
- **Experimental method + headline numbers:** [`docs/methodology.md`](docs/methodology.md)
- **Status, decisions, open work:** [`PROGRESS.md`](PROGRESS.md)
- **Track B setup details:** [`mininet/README.md`](mininet/README.md)
- **Latest evaluator output:** [`results/summary.csv`](results/summary.csv), [`results/deltas.csv`](results/deltas.csv), [`results/figures/`](results/figures/)

If something in those docs disagrees with this hand-off file, **the docs win** — they are the maintained source of truth. This hand-off file is a snapshot to bootstrap a fresh agent into the project.
