# AI-Driven Predictive Network Traffic Management for 6G Using Edge Intelligence

**Submission category:** Idea / Abstract — Intelligent Network: AI-Driven
Connectivity for the Next Generation (White Paper 2026).

## Problem

Traditional traffic management systems in telecommunication networks are
fundamentally **reactive**: congestion is detected only after queues build up,
which already manifests as higher latency, packet loss, degraded QoS and
inefficient link utilization. As networks evolve toward 6G — with ultra-low
latency targets, massive device density and AI-native control planes — a
reactive paradigm becomes structurally incompatible with the expected service
guarantees. The next generation of intelligent networks requires control loops
that **anticipate** congestion and act before it materialises.

## Proposed Idea

We propose an **AI-native predictive traffic management** framework that
shifts the control loop from "detect-then-react" to **"forecast-then-prevent"**,
implemented with **edge intelligence** so that decisions are taken close to
the source switch rather than in a remote cloud controller.

The framework has four cooperating components:

1. **Edge metrics collector** — gathers per-link throughput, latency, packet
   loss and utilization as a continuous time series.
2. **LSTM forecaster** — a small, explainable recurrent model that predicts
   per-link utilization for the next short horizon from a sliding window of
   recent observations.
3. **Rule-based decision engine** — converts forecasts into a binary
   `KEEP` / `REROUTE` decision using a configurable threshold and hysteresis
   to suppress flapping.
4. **SDN-style routing layer** — applies the decision: under `REROUTE`, flows
   are programmatically shifted onto an alternate path whose forecast peak
   utilization is lowest.

The decision logic deliberately runs as a logical *edge agent*, co-located
with the source-side switch, so that the predict-decide-act loop completes
within a single tick and is compatible with the ultra-low-latency control
plane envisioned for 6G.

## Methodology

We instantiate the framework as a discrete-time, fluid-model simulation
written in pure Python (NetworkX + TensorFlow/Keras). The topology is a
multipath three-switch network with a deliberately narrow primary link
(100 Mbps) and a slightly higher-latency but wider alternate path
(180 Mbps), so that any policy decision — single-path or multi-path —
has a measurable effect.

We compare *three* routing policies under the same simulator,
topology, traffic and metrics, deliberately bracketing the prediction
problem on both sides:

|             | Static (shortest path) | ECMP (50/50 multi-path) | Predictive (LSTM + decision engine) |
|-------------|------------------------|-------------------------|-------------------------------------|
| Normal      | `normal_static`        | `normal_ecmp`           | `normal_predictive`                 |
| Congestion  | `congestion_static`    | `congestion_ecmp`       | `congestion_predictive`             |

For each, we measure average latency, p95 latency, delivered
throughput, average packet loss, and the fraction of ticks spent above
90 % link utilization (a proxy for congestion event duration). The
LSTM's forecast quality is reported separately as MAE / RMSE on a
held-out window. Including ECMP — the de-facto industry standard for
static multipath load balancing — as a second WITHOUT-AI baseline
prevents the AI-vs-static comparison from looking artificially easy
and forces an honest reading of when the predictive policy is, and is
not, the right tool.

The same CSV schema and the same decision engine are wired to a
parallel Mininet + Ryu reference implementation (see
[`mininet/`](../mininet/)) so the full paper can validate the approach
on a real OpenFlow data plane without re-engineering the AI pipeline.

## Results

The current end-to-end run on the prototype reproduces the expected
pattern. Under the **normal** scenario, the predictive policy is
provably idle (zero reroute decisions across 600 ticks), so its metrics
are identical to the static baseline; ECMP intentionally pays a small
constant latency penalty (≈2 ms) to keep both paths warm.

Under the **congestion** scenario, both smart policies dramatically
out-perform static shortest-path routing. Relative to the static
baseline:

| metric                          | Static (baseline) | ECMP        | Predictive (LSTM) |
|---------------------------------|------------------:|------------:|------------------:|
| Avg end-to-end latency (ms)     | 38.40             | 5.87 (−85 %)| 15.57 (−59 %)     |
| p95 latency (ms)                | 100.23            | 8.33 (−92 %)| 100.22 (−0 %)     |
| Avg packet loss (%)             | 5.65              | 0.00 (−100 %)| 0.91 (−84 %)     |
| Congestion ticks ≥ 0.9 util (%) | 35.33             | 0.00 (−100 %)| 6.50 (−82 %)     |
| Avg throughput (Mbps)           | 52.15             | 59.12 (+13 %)| 58.00 (+11 %)    |

**The honest finding the paper has to lead with** is that on this
topology and burst profile, ECMP is *also* an excellent congestion
mitigator — because the alternate path's 180 Mbps capacity is wide
enough that statically splitting 50/50 absorbs the entire 120 Mbps
burst without saturating either branch. The predictive policy beats
static by a wide margin, but trails ECMP on average latency and on the
p95 tail, because it pays a brief **detection-lag tax** at the start
of each burst — a few burst ticks land on the saturated primary link
before the LSTM swings traffic over.

This is a healthy, falsifiable finding rather than a weakness: it
identifies the regimes where AI-driven prediction is expected to
dominate ECMP — namely (i) topologies where a blind 1/N split *also*
saturates the alternate path, (ii) realistic flow counts where
hash-based ECMP cannot achieve the ideal 1/N split in practice, and
(iii) settings where the best path is time-varying so any static split
is structurally wrong. Characterising those regimes is the headline
experiment for the full paper.

The LSTM achieves MAE 0.013 and RMSE 0.024 on held-out per-link
utilization forecasts. All numbers are reproducible end-to-end from
[`results/summary.csv`](../results/summary.csv) by running
`python scripts/run_all.py`.

## Relevance to 6G

The contribution targets the **AI-native, edge-intelligent, self-optimising**
pillars of the 6G vision. Rather than treat AI as an analytics add-on, the
LSTM and decision engine are *inside* the routing control loop and *at* the
edge, foreshadowing how future networks can ship intelligence into the
network fabric itself.

## Future Work

The rule-based engine is a deliberately minimal first step. Replacing it
with a reinforcement-learning controller, federating multiple edge agents,
and validating on a real 5G/6G testbed are natural extensions for the full
paper and follow-on work.
