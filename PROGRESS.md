# Project Progress Tracker

**Project:** AI-Driven Predictive Network Traffic Management for 6G Using Edge Intelligence
**Current phase:** Phase 2 — Full white paper (Track B validated; prose + extras + submission remain)
**Overall status:** Track A (simulator) **and** Track B (real Mininet/OpenFlow data plane) both built, executed, evaluated, and documented. Trends match across tracks. Remaining work is paper prose, optional stress experiments, formatting, and submission.

---

## 1. What has been done (technical work)

### Code and artifacts — all complete

| Area | Where | Status |
|---|---|---|
| Project scaffold + dependencies | [requirements.txt](requirements.txt), [README.md](README.md) | Done |
| Central config (knobs, topology, ML hparams) | [src/config.py](src/config.py) | Done |
| NetworkX topology + path enumeration | [src/simulation/topology.py](src/simulation/topology.py) | Done |
| Traffic generator (normal / burst / mixed-training) | [src/simulation/traffic_generator.py](src/simulation/traffic_generator.py) | Done |
| Discrete-time fluid simulator | [src/simulation/simulator.py](src/simulation/simulator.py) | Done |
| CSV collector + sliding-window preprocessor | [src/data/](src/data/) | Done |
| Keras LSTM model + training + persisted weights | [src/ml/](src/ml/), [models/lstm.keras](models/lstm.keras) | Done |
| Decision engine (threshold + hysteresis) | [src/decision/engine.py](src/decision/engine.py) | Done |
| Static + predictive routers | [src/sdn/](src/sdn/) | Done |
| ECMP baseline router (weighted 50/50 multi-path) | [src/sdn/router_ecmp.py](src/sdn/router_ecmp.py) | Done |
| Weighted-multipath simulator + per-tick flow metrics | [src/simulation/simulator.py](src/simulation/simulator.py) | Done |
| Metric aggregation + N-way policy comparison (static / ECMP / predictive) | [src/evaluation/](src/evaluation/) | Done |
| 11 matplotlib comparison plots (3-policy bars + 3-series timeseries) | [results/figures/](results/figures/) | Done |
| Numbered pipeline scripts 01, 02, 03, 03b, 04, 05 + run_all.py | [scripts/](scripts/) | Done |
| Abstract / Architecture / Methodology / References docs | [docs/](docs/) | Done |
| Mininet topology (3 OVS bridges, host namespaces, runtime.json) | [mininet/topology.py](mininet/topology.py) | **Done — executed** |
| os-ken / Ryu OpenFlow 1.3 controller (static + predictive modes, full `_maybe_reroute()`) | [mininet/ryu_app.py](mininet/ryu_app.py), [mininet/run_controller.py](mininet/run_controller.py) | **Done — executed** |
| UDP iperf3 traffic driver (normal + 3-burst congestion profile) | [mininet/traffic_iperf.sh](mininet/traffic_iperf.sh) | **Done — executed** |
| Metrics collector (OVS port counters + ping RTT → Track A-compatible CSV) | [mininet/collect_metrics.py](mininet/collect_metrics.py) | **Done — executed** |
| Track B run write-up + Track A trend comparison | [docs/track_b.md](docs/track_b.md) | **Done** |

### Achievement numbers

#### Track A (analytical simulator) — three-way policy comparison, `congestion` scenario

| metric | Static | ECMP | Predictive |
|---|---:|---:|---:|
| avg latency (ms) | 38.40 | **5.87** (−85 %) | 15.57 (−59 %) |
| p95 latency (ms) | 100.23 | **8.33** (−92 %) | 100.22 (−0 %) |
| avg packet loss (%) | 5.65 | **0.00** (−100 %) | 0.91 (−84 %) |
| congestion ticks (%) | 35.33 | **0.00** (−100 %) | 6.50 (−82 %) |
| avg throughput (Mbps) | 52.15 | **59.12** (+13 %) | 58.00 (+11 %) |

Honest finding: on **this** topology / burst profile, ECMP also dominates static — its alternate path is wide enough that a static 50/50 split absorbs the burst. Predictive beats static by a wide margin but trails ECMP because it pays a *detection-lag tax* (a few burst ticks land on the saturated primary before the LSTM swings traffic over, visible in the p95 tail). Phase 2 will add stress topologies where ECMP's blind 1/N split saturates the alternate path — that is the regime where the predictive controller is expected to dominate ECMP.

- LSTM held-out: MAE 0.013, RMSE 0.024 on utilization in [0, 1]
- Normal scenario: predictive policy takes **0** reroute actions (proves it doesn't disturb non-congested traffic)
- Congestion scenario: **185** reroute decisions, all inside the burst windows

Archived Track A artifacts: [`track_a_archive/data/`](track_a_archive/data/), [`track_a_archive/results/`](track_a_archive/results/) — preserved verbatim after the live `data/` / `results/` were repurposed for Track B's evaluator run.

#### Track B (real Mininet/OpenFlow data plane) — predictive vs static, `congestion` scenario

Captured 2026-06-15 on real OVS 3.3.4 + os-ken controller + iperf3 UDP traffic. Full write-up: [`docs/track_b.md`](docs/track_b.md).

| metric | Track B static | Track B predictive | delta | Track A delta (same metric) | direction match? |
|---|---:|---:|---:|---:|:-:|
| avg latency (ms) | 49.12 | 25.07 | **−48.96 %** | −59.45 % | ✓ |
| p95 latency (ms) | 188.2 | 122.0 | **−35.18 %** | −0.01 % | ✓ (Track B *stronger* — real HTB queues are uncapped) |
| avg throughput (Mbps) | 46.69 | 54.61 | **+16.97 %** | +11.22 % | ✓ |
| congestion ticks (%) | 18.36 | 10.83 | **−41.01 %** | −81.60 % | ✓ |
| max utilization | 1.083 | 1.000 | −7.64 % | −4.45 % | ✓ |
| avg packet loss (%) | 0.00 | 0.00 | — | −83.92 % | partial — qdisc-blind, recoverable via throughput differential (see `docs/track_b.md` §7) |

- 4/600 ticks per run; 5 links per tick; 18-column Track A schema, evaluator-compatible
- During the three burst windows, predictive engaged the alternate path for **145/600 ticks (24 %)**, in three contiguous clusters aligned with bursts 1/2/3 — the LSTM caught burst 1 onset at tick **117**, three seconds before iperf3 actually started its burst
- During congestion bursts, primary-path latency in static spikes to 175 ms; in predictive it stays at ~13 ms (the alternate path's two-hop baseline)
- Throughput differential at s1 (offered − carried): static loses **15.4 Mbps**, predictive loses **4.2 Mbps** → **73 % reduction in dropped throughput**, same direction as Track A's loss delta

**Conclusion: Track A's analytical result holds up under real OpenFlow flow rewrites, real Linux qdisc queues, and a real controller-to-switch round trip.**

---

## 2. Concepts learned / doubts cleared (FAQ log)

Running log of the conceptual questions answered so far. Use this as a study reference.

**Networking fundamentals**
- What latency, throughput, packet loss, capacity, utilization mean
- What a queue is (the buffer inside every switch/router port)
- Why queues are the root cause of latency spikes and packet loss
- The queueing-theory formula `latency = base_latency / (1 − utilization)`
- How big buffers are in real devices (KB on home routers to GBs on core routers)
- Bufferbloat — the downside of oversized buffers
- What happens traditionally when packets overload a cable (buffer fills → tail-drop → TCP backoff)
- AQM (RED, CoDel), ECN, QoS — the existing reactive mitigations

**Protocols**
- TCP reassembly using sequence numbers — happens in the receiver's kernel TCP stack
- Reordering happens **once**, in TCP (not in the app layer)
- UDP doesn't reorder — apps add their own sequence numbers (RTP, game protocols)
- Jitter buffer, packet-loss concealment, NACK, lag compensation
- TCP vs UDP trade-off (reliability vs speed)
- End-to-end principle — network does the minimum, endpoints do the rest

**Hardware**
- Multi-queue NICs and RSS (Receive Side Scaling)
- Why parallel queues exist (one core per queue)
- DPDK / XDP / eBPF as kernel-bypass options
- SmartNICs / DPUs in 5G core equipment

**Our project's mechanics**
- A "tick" = 1 simulated second
- "Fake" was a poor word — we use a fluid-level simulator (a legitimate research methodology)
- Track A = math/fluid, Track B (Mininet) = real packets through Open vSwitch
- Architecture diagram is **two modes**: train-once + run-experiments-four-times
- "3-switch multipath" = 3 switches arranged so two parallel paths exist between source and destination
- Why the alternate path is wider than the primary (must be a relief valve, not a worse path)

**Telecom architecture**
- RAN vs O-RAN (O-RAN is open, disaggregated, AI-native; not a generation)
- RU / DU / CU disaggregation in O-RAN
- Fronthaul (RU↔DU), Midhaul (DU↔CU), Backhaul (CU↔Core)
- RIC = RAN Intelligent Controller; runs xApps (Near-RT, 10ms–1s) and rApps (Non-RT, >1s)
- A1 and E2 interfaces
- Our project = structural equivalent of an O-RAN **Near-RT RIC xApp**
- **Control plane vs data plane** — our project is control plane, observes data plane, doesn't sit in user data path

**SDN reality check**
- SDN is production-deployed (Google B4, Microsoft SWAN, AT&T, datacenters)
- OpenFlow / Open vSwitch / Ryu / ONOS / OpenDaylight are real, production-grade
- Mininet is the industry-standard SDN research simulator (vs Packet Tracer, which is educational only)

**OSI layer placement**
- Our routing decision sits at **L3 (Network Layer)** — classic traffic engineering
- SDN can blur L2/L3/L4 through flow-table matches

**Paper-quality assessment**
- Honest tier: strong middle-to-upper tier industrial white paper
- Strongest with Track B executed + a second baseline added
- Curated reading list of 12 papers handed over (Letaief 2019, Polese 2023, Azzouni 2017 are the top 3)

---

## 3. Learning ladder — progress

| # | Step | Status |
|---|---|---|
| 1 | The problem we're solving (reactive vs predictive, 6G framing) | Done |
| 2 | Big-picture architecture walkthrough (15 boxes, two modes) | Done |
| 3 | Network anatomy (hosts, switches, links, capacity, latency, topology) | Done |
| 4 | How we simulate traffic (discrete-time, normal/burst, background flows) | Pending |
| 5 | What data we collect (CSV columns and meaning) | Pending |
| 6 | The AI part (time-series prediction, LSTM, windowing, MAE/RMSE) | Pending |
| 7 | The decision engine (threshold, hysteresis) | Pending |
| 8 | The two routers + SDN (static vs predictive, SDN realism) | Pending |
| 9 | The evaluation (4 experiments, reading the metrics) | Pending |
| 10 | The Mininet / Ryu stretch (what those tools are and the demo) | **Done** — covered live during Track B execution (Mininet namespaces/veth/HTB, OpenFlow flow tables, OVS port counters, os-ken vs Ryu, live LSTM reroute demo) |

**Resume the ladder anytime by saying "Step N please".**

---

## 4. Design decisions already locked in

These are settled — no need to revisit unless something changes.

- **Title:** *AI-Driven Predictive Network Traffic Management for 6G Using Edge Intelligence*
- **Track A** (pure-Python fluid simulator) is the primary submission evidence
- **Track B** (Mininet + Ryu) is the stretch / full-paper validation
- **LSTM architecture:** LSTM(64) → Dropout → LSTM(32) → Dropout → Dense(5); window=20, horizon=5; Adam, MSE, 25 epochs
- **Congestion threshold:** 0.75 with 0.10 hysteresis band, min reroute duration 5 ticks
- **Topology:** 3-switch multipath; primary `s1↔s2` at 100 Mbps / 2 ms; alternate `s1↔s3↔s2` at 180 Mbps / 3 ms each
- **Burst scenario:** mean 120 Mbps, std 8 Mbps, three burst windows inside a 600-tick run
- **Decision engine:** rule-based (RL explicitly deferred to future work)
- **Architectural framing:** Control-plane, O-RAN Near-RT RIC xApp
- **Reproducibility seed:** `CONFIG.simulation.random_seed = 42`

---

## 5. Open decisions (not yet made)

These need your input when you're ready.

- Final contest template / required word count for the abstract
- Whether to add the **stress topology** experiment (ECMP-vs-predictive on a narrow alternate path) in Phase 2 or defer
- Whether to add an **oracle / future-aware** upper-bound baseline in Phase 2
- Whether to add **ECMP under Mininet** to round out Track B's 3-way comparison
- Whether to pursue Phase 3 (containerized xApp on O-RAN SC) at all
- Whether to add an RL decision-engine variant in Phase 2 or defer fully

---

## 6. Goals and deliverables

### Phase 1 — Abstract submission (immediate)

| Deliverable | Status |
|---|---|
| Abstract document with real numbers | Done — [docs/abstract.md](docs/abstract.md) |
| Architecture diagram (mermaid) | Done — [docs/architecture.md](docs/architecture.md) |
| 2-3 hero plots for the abstract | Done — [results/figures/](results/figures/) |
| Title | Done |
| Confirm contest template / format | Pending |
| Submit | Pending |

### Phase 2 — Full white paper (2-3 weeks after abstract is accepted)

| Deliverable | Status |
|---|---|
| Track B executed (Mininet + Ryu real-packet run) | **Done** — [docs/track_b.md](docs/track_b.md), 4 CSVs in [data/](data/), 11 figures in [results/figures/](results/figures/) |
| Trends validated to match Track A | **Done** — 5/6 metrics show direction agreement at comparable magnitudes; loss column has documented HTB caveat |
| Second baseline added (ECMP) | Done — [src/sdn/router_ecmp.py](src/sdn/router_ecmp.py) |
| Third baseline added (oracle / future-aware) | Pending |
| ECMP also executed under Mininet (Track B 3-way comparison) | Pending — currently Track B is static-vs-predictive only |
| Stress topology where ECMP saturates the alternate path (predictive should win) | Pending |
| Optional: larger random topology (10–20 switches) | Pending |
| Introduction prose | Pending |
| Related-work prose (using the 12-paper reading list) | Pending |
| Conclusion + future-work prose | Pending |
| Format to contest template | Pending |
| Internal review pass | Pending |
| Final submission | Pending |

### Phase 3 — Stretch / exceptional (optional, ~1 month)

| Deliverable | Status |
|---|---|
| Containerized xApp deployable on O-RAN SC Near-RT RIC | Pending — optional |
| RL-based decision-engine variant (PPO / DQN) | Pending — optional |
| Validation on public traffic dataset (Abilene / CAIDA) | Pending — optional |

---

## 7. Pending task list (concrete)

### Phase 1 — this week
- [ ] Confirm the contest's submission template, word count, and required sections
- [ ] Adapt [docs/abstract.md](docs/abstract.md) to that template (if different)
- [ ] Submit the abstract

### Phase 2 — after abstract accepted
- [x] Install Mininet + Open vSwitch + os-ken + iperf3 on WSL2 *(2026-06-12)*
- [x] Verify [mininet/topology.py](mininet/topology.py) brings up the topology with `pingall` *(2026-06-12)*
- [x] Wire the trained LSTM into [mininet/ryu_app.py](mininet/ryu_app.py)'s predictive hook *(2026-06-12; full `_maybe_reroute()` implemented + verified live)*
- [x] Run normal + congestion scenarios under Mininet *(2026-06-15; 4 CSVs captured)*
- [x] Confirm Mininet CSV trends match Track A *(2026-06-15; see [docs/track_b.md §6](docs/track_b.md))*
- [x] Add ECMP baseline to [src/sdn/](src/sdn/) and re-run evaluation *(Track A only)*
- [ ] Add stress topology where blind 50/50 ECMP saturates the alternate path
      (so the predictive policy's selective rerouting can dominate ECMP, not just static)
- [ ] (Optional) Add oracle / future-aware baseline as an upper bound
- [ ] (Optional) Execute ECMP under Mininet to complete Track B's 3-way comparison
- [ ] Optional: 10-switch random topology experiment
- [ ] Write *Introduction* section
- [ ] Write *Related Work* section (use the 12-paper reading list)
- [ ] Write *Conclusion + Future Work* section
- [ ] Format paper to contest template
- [ ] Polish figures (consistent style, captions, axis labels)
- [ ] Internal review
- [ ] Submit full paper

### Phase 3 — optional, only if time and ambition allow
- [ ] Containerize the LSTM + decision engine as an O-RAN xApp
- [ ] Deploy on O-RAN Software Community Near-RT RIC sandbox
- [ ] Implement PPO/DQN RL decision engine and benchmark vs rule-based
- [ ] Run on public dataset (e.g., Abilene traffic matrix)

---

## 8. Indicative timeline

| Week | Milestone | Status |
|---|---|---|
| W0 | Confirm contest template + submit abstract | Pending |
| W1–W2 | Execute Track B (Mininet + Ryu) | **Done** (2026-06-12 → 2026-06-15) |
| W3 | Stress topology + (optional) larger topology + (optional) Track B ECMP | Pending |
| W4 | Write Intro / Related-Work / Conclusion prose | Pending |
| W5 | Format, polish, internal review, submit full paper | Pending |
| W6–W9 (optional) | Phase 3 — xApp containerization + RL variant | Pending |

---

## 9. Quick links

- [README](README.md) — overview + how to run
- [Abstract](docs/abstract.md) — submission-ready
- [Architecture](docs/architecture.md) — diagrams + module map
- [Methodology](docs/methodology.md) — full Track A method + headline results
- [Track B write-up](docs/track_b.md) — real Mininet/OpenFlow validation + Track A trend comparison
- [References](docs/references.md) — seed bibliography
- [Results summary](results/summary.csv) — currently Track B per-experiment numbers
- [Static-vs-predictive deltas](results/deltas.csv) — currently Track B deltas
- [Plots](results/figures/) — 11 PNGs (currently Track B)
- [Track A archive](track_a_archive/) — original Track A `data/` and `results/` preserved verbatim
- [Mininet README](mininet/README.md) — Track B setup + run commands
- [Source tree](src/) — all Track A modules
- [Mininet directory](mininet/) — all Track B modules
- [Pipeline scripts](scripts/) — 01-05 + run_all.py

---

*Last updated: 2026-06-15 (Track B end-to-end run executed, trends-match validated, [docs/track_b.md](docs/track_b.md) drafted; live `data/`/`results/` are now Track B, Track A originals safe in `track_a_archive/`). Maintain this file by editing it directly; do not regenerate.*
