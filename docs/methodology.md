# Methodology

This document describes the experimental setup precisely enough that the
white paper's evaluation section can be derived from it directly, and so
that the results in [`results/summary.csv`](../results/summary.csv) and
[`results/figures/`](../results/figures/) are reproducible.

## 1. Network model

We model the network as an undirected weighted graph
(see [`src/simulation/topology.py`](../src/simulation/topology.py)).
Hosts and switches are nodes; physical links are edges carrying
`capacity_mbps` and `base_latency_ms` attributes.

Topology declared in [`src/config.py`](../src/config.py):

- Hosts: `h1`, `h2`.
- Switches: `s1`, `s2`, `s3`.
- Edges:
  - `h1 - s1` and `s2 - h2`: 1 Gbps access links, 0.1 ms.
  - `s1 - s2`: 100 Mbps primary backbone link, 2.0 ms (low-latency bottleneck).
  - `s1 - s3` and `s3 - s2`: 180 Mbps alternate-path links, 3.0 ms each
    (relief valve — higher capacity, slightly higher base latency).
- Default path (shortest by base latency): `h1 - s1 - s2 - h2`.
- Alternate path: `h1 - s1 - s3 - s2 - h2`.

## 2. Traffic model

Two evaluation scenarios and one training scenario are defined in
[`src/simulation/traffic_generator.py`](../src/simulation/traffic_generator.py):

- **Normal** — Gaussian offered load around 25 Mbps (std 6).
- **Congestion** — same baseline plus three scheduled burst windows of
  ~120 Mbps mean, each with a ramp-in.
- **Training (mixed)** — long trace with periodic synthetic bursts so the
  LSTM observes both regimes.

The simulator additionally injects a small number of persistent background
flows on inter-switch edges to make the time series realistically noisy.

## 3. Simulator

Discrete-time fluid model
(see [`src/simulation/simulator.py`](../src/simulation/simulator.py)):

- One tick per simulated second.
- At each tick, the active routing policy returns a path; the offered load
  is mapped onto every edge of that path; background flows add cross-
  traffic; each edge then computes utilization, latency and loss using a
  light-weight M/M/1-flavoured queueing approximation.
- The simulator emits one `TickRecord` per (tick, edge), which is converted
  to the canonical CSV schema by
  [`src/data/collector.py`](../src/data/collector.py).

## 4. Dataset construction

[`src/data/preprocessor.py`](../src/data/preprocessor.py) builds windowed
supervised-learning samples:

- Features per timestep: `throughput_mbps`, `latency_ms`,
  `packet_loss_pct`, `utilization` (4 features).
- Window size `N = 20` ticks.
- Horizon `k = 5` ticks.
- Each link contributes its own time series; samples from all links are
  concatenated into a single dataset that the LSTM trains on, so the model
  generalises across links.
- MinMax scalers are fit on the training split and persisted.

Train / val / test splits are 70 / 15 / 15 along the time axis.

## 5. LSTM forecaster

[`src/ml/lstm_model.py`](../src/ml/lstm_model.py):

```
Input(N=20, F=4)
LSTM(64, return_sequences=True)  +  Dropout(0.1)
LSTM(32)                          +  Dropout(0.1)
Dense(k=5, linear)
```

Optimiser Adam, learning rate `1e-3`, loss MSE, batch size 64, 25 epochs.
The model is intentionally small so it (a) trains in minutes on CPU and
(b) is plausible as an *edge*-deployed inference module.

Training artifacts are saved under
[`models/`](../models/): `lstm.keras`, `feature_scaler.pkl`,
`target_scaler.pkl`, `metadata.json`.

## 6. Decision engine

[`src/decision/engine.py`](../src/decision/engine.py) — single rule with
hysteresis:

```
IF predicted_peak_utilization >= 0.75   ->  REROUTE
ELIF currently_rerouted AND predicted_peak < 0.65
     AND ticks_since_change >= 5         ->  KEEP (return to default)
ELSE                                     ->  maintain previous action
```

This is a deliberately *explainable* control law for the first paper.
Replacing it with an RL policy is documented as future work.

## 7. Routing policies

We compare three policies, two of which are WITHOUT-AI controls:

- [`src/sdn/router_static.py`](../src/sdn/router_static.py) — single
  shortest path by base latency, fixed for the whole experiment. The
  classical WITHOUT-AI control: it cannot exploit the alternate path at
  all.
- [`src/sdn/router_ecmp.py`](../src/sdn/router_ecmp.py) — Equal-Cost
  Multi-Path. The offered load is split *equally and statically* across
  every simple path between source and destination (here, 50 % primary
  + 50 % alternate). This is the de-facto industry baseline for static
  multipath load balancing in data-center fabrics and many ISP
  backbones, and is a much harder baseline than single-path static
  routing because it already uses both physical paths. Modelled at the
  fluid level: we feed each tick's offered load through the simulator
  as `[(primary, 0.5), (alternate, 0.5)]`, which matches the long-run
  steady state of hash-based ECMP when there are many flows. Per-tick
  flow-level metrics (`effective_latency_ms`,
  `effective_throughput_mbps`, `effective_packet_loss_pct`) are
  computed inside the simulator as weight-aware aggregates over the
  chosen paths, so that ECMP is reported as a single end-to-end flow
  rather than as the worst-link bottleneck on either branch.
- [`src/sdn/router_predictive.py`](../src/sdn/router_predictive.py) — at
  every tick, forecast utilization on the default path; if the decision
  engine returns `REROUTE`, pick the alternate path with the lowest
  predicted peak utilization. Decisions are logged to
  `results/{scenario}_predictive_decisions.json`.

The simulator's routing interface accepts both single-path and
weighted-multipath decisions, so single-path routers (static, predictive)
do not need to know the multipath code path exists.

## 8. Experiments

The 2 × 3 factorial design (scenario × policy) gives six runs:

| Scenario   | Policy      | CSV                                                                                              |
|------------|-------------|--------------------------------------------------------------------------------------------------|
| Normal     | Static      | [`data/normal_static.csv`](../data/normal_static.csv)                                            |
| Normal     | ECMP        | [`data/normal_ecmp.csv`](../data/normal_ecmp.csv)                                                |
| Normal     | Predictive  | [`data/normal_predictive.csv`](../data/normal_predictive.csv)                                    |
| Congestion | Static      | [`data/congestion_static.csv`](../data/congestion_static.csv)                                    |
| Congestion | ECMP        | [`data/congestion_ecmp.csv`](../data/congestion_ecmp.csv)                                        |
| Congestion | Predictive  | [`data/congestion_predictive.csv`](../data/congestion_predictive.csv)                            |

## 9. Metrics

Computed by [`src/evaluation/metrics.py`](../src/evaluation/metrics.py)
on the *active path* of each tick:

- `avg_latency_ms`, `p95_latency_ms` — quality-of-service tail behaviour.
- `avg_throughput_mbps`, `total_throughput_gbits` — effective delivered
  throughput.
- `avg_packet_loss_pct` — congestion impact on reliability.
- `congestion_event_duration_pct` — fraction of ticks with utilization at
  or above 0.9.
- `avg_utilization`, `max_utilization` — efficiency signal.

LSTM forecast quality is reported in `models/metadata.json`
(`test_mse`, `test_mae`, `test_rmse_utilization`).

## 10. Comparison and figures

[`src/evaluation/compare.py`](../src/evaluation/compare.py) computes
`(policy - static) / static` for every non-static policy in the table
(predictive, ECMP, …) so the comparison generalises to N baselines.
[`src/visualization/plots.py`](../src/visualization/plots.py) renders the
following figures into `results/figures/`:

- `latency_timeseries_{scenario}.png`
- `throughput_timeseries_{scenario}.png`
- `utilization_timeseries_{scenario}.png`
- `bars_avg_latency_ms.png`, `bars_p95_latency_ms.png`,
  `bars_avg_throughput_mbps.png`, `bars_avg_packet_loss_pct.png`,
  `bars_congestion_event_duration_pct.png`.

## 11. Reproducibility

- All randomness is seeded via `CONFIG.simulation.random_seed`.
- The full pipeline runs end-to-end with `python scripts/run_all.py`.
- Track B (Mininet + Ryu) reproduces the same CSV schema so the same LSTM
  and decision engine apply unchanged on a real OpenFlow data plane.

## 12. Headline results (from the current run)

| Scenario   | Policy      | Avg latency (ms) | p95 latency (ms) | Throughput (Mbps) | Avg packet loss (%) | Congestion ticks (%) | Max utilization |
|------------|-------------|-----------------:|-----------------:|------------------:|--------------------:|---------------------:|----------------:|
| Normal     | Static      | 2.88             | 3.27             | 24.84             | 0.00                | 0.00                 | 0.42            |
| Normal     | ECMP        | 4.81             | 5.00             | 24.84             | 0.00                | 0.00                 | 0.28            |
| Normal     | Predictive  | 2.88             | 3.27             | 24.84             | 0.00                | 0.00                 | 0.42            |
| Congestion | Static      | 38.40            | 100.23           | 52.15             | 5.65                | 35.33                | 1.45            |
| Congestion | ECMP        | 5.87             | 8.33             | 59.12             | 0.00                | 0.00                 | 0.73            |
| Congestion | Predictive  | 15.57            | 100.22           | 58.00             | 0.91                | 6.50                 | 1.39            |

### Predictive vs. static (congestion scenario)

- average end-to-end latency: **−59.5 %** (38.40 → 15.57 ms)
- average packet loss: **−83.9 %** (5.65 → 0.91 %)
- congestion event duration: **−81.6 %** (35.33 → 6.50 % of ticks)
- average throughput: **+11.2 %** (52.15 → 58.00 Mbps)
- peak utilization: **−4.5 %** (1.45 → 1.39)

### ECMP vs. static (congestion scenario)

- average end-to-end latency: **−84.7 %** (38.40 → 5.87 ms)
- average packet loss: **−100 %** (5.65 → 0.00 %)
- congestion event duration: **−100 %** (35.33 → 0.00 % of ticks)
- average throughput: **+13.4 %** (52.15 → 59.12 Mbps)
- peak utilization: **−50.0 %** (1.45 → 0.73)

### Discussion

Both AI-free and AI-based smart policies dramatically out-perform static
shortest-path routing under congestion; the headline is that **just
naively spreading load across both paths beats single-path routing by a
wide margin.** That establishes the floor.

In this *particular* topology and burst profile, ECMP also out-performs
the predictive policy: the alternate path's 180 Mbps capacity is wide
enough that a static 50/50 split absorbs the entire 120 Mbps burst
without saturating either branch, while the predictive policy pays a
**detection-lag tax** — a few burst ticks land on the saturated primary
link before the LSTM-driven decision engine swings traffic over, which
is visible in the predictive policy's p95 latency tail. This is a
healthy and instructive result: the predictive controller is expected
to dominate ECMP under conditions where a static split is *not* safe,
namely when

1. background traffic on the alternate path makes a uniform 1/N split
   risky (any blind split would over-commit the second path), or
2. flow counts are low so hash-based ECMP cannot achieve the ideal
   1/N split in practice, or
3. multiple bottleneck links exist and the best path is time-varying.

Constructing and reporting these stress topologies is on the roadmap
for the full paper.

LSTM forecaster quality (held-out test set, scaled to [0,1]
utilization): MAE 0.013, RMSE 0.024.

The predictive router takes **0** reroute decisions in the normal
scenario and **185** in the congestion scenario.

All numbers are regenerated by `python scripts/run_all.py` and
persisted to [`results/summary.csv`](../results/summary.csv) and
[`results/deltas.csv`](../results/deltas.csv).
