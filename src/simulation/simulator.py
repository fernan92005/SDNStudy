"""Discrete-time network simulator.

The simulator advances one tick at a time. Per tick:

  1. The active routing policy chooses a path from source to destination.
  2. Background flows add cross-traffic on edges they touch.
  3. Offered load is mapped onto every edge of the chosen path.
  4. Each edge computes utilization, latency (queueing), packet loss, and
     measured throughput; results are emitted as a row of per-link metrics.

This is intentionally a fluid-model simulator (no per-packet detail) — it's
the right level of abstraction for an LSTM-driven control loop and matches
the per-second granularity at which an SDN controller would actually act.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import networkx as nx
import numpy as np

from src.config import CONFIG, SimulationConfig
from src.simulation.topology import edge_key, path_edges
from src.simulation.traffic_generator import TrafficScenario


PathDecision = List[Tuple[List[str], float]]
RoutingPolicy = Callable[["RoutingContext"], "PolicyOutput"]
PolicyOutput = object  # see _normalize_policy_output below


@dataclass
class RoutingContext:
    """Information passed to a routing policy at each tick."""

    tick: int
    graph: nx.Graph
    source: str
    destination: str
    recent_utilization: Dict[Tuple[str, str], float]
    offered_load_mbps: float


@dataclass
class TickRecord:
    tick: int
    scenario: str
    policy: str
    link: Tuple[str, str]
    offered_load_mbps: float
    carried_load_mbps: float
    capacity_mbps: float
    utilization: float
    latency_ms: float
    packet_loss_pct: float
    path_index: int
    is_on_active_path: bool
    # Per-tick flow-level metrics (same value replicated on every row of the
    # tick). Required for weighted multi-path policies (e.g. ECMP) where
    # naive aggregation across active edges double-counts paths and reports
    # bottlenecks instead of summed flow throughput. For single-path
    # policies these reduce to the natural per-path values.
    effective_latency_ms: float
    effective_throughput_mbps: float
    effective_packet_loss_pct: float


def _normalize_policy_output(out: object) -> PathDecision:
    """Accept either a single path (List[str]) or a list of (path, weight)
    tuples. Returns the weighted form.

    Backward-compatible with single-path policies (static, predictive),
    while enabling multi-path policies like ECMP.
    """
    if not out:
        raise ValueError("Routing policy returned an empty decision")
    if isinstance(out, list) and isinstance(out[0], str):
        return [(list(out), 1.0)]
    decision: PathDecision = []
    for item in out:
        if (
            isinstance(item, tuple)
            and len(item) == 2
            and isinstance(item[0], list)
        ):
            path, weight = item
            decision.append((list(path), float(weight)))
        else:
            raise TypeError(f"Unsupported routing policy output element: {item!r}")
    total_w = sum(w for _, w in decision)
    if total_w <= 0:
        raise ValueError("Routing policy weights must sum to a positive value")
    return [(p, w / total_w) for p, w in decision]


def _link_metrics(
    offered_mbps: float, capacity_mbps: float, base_latency_ms: float
) -> Tuple[float, float, float, float]:
    """Compute (carried, utilization, latency_ms, loss_pct) for an edge.

    A simple M/M/1-flavoured queueing approximation: latency rises sharply as
    utilization approaches 1.0; loss is zero below capacity and linear above.
    """
    utilization = offered_mbps / capacity_mbps if capacity_mbps > 0 else 0.0
    if utilization < 1.0:
        carried = offered_mbps
        queue_factor = 1.0 / max(1e-3, 1.0 - utilization)
        latency = base_latency_ms * queue_factor
        loss = 0.0
    else:
        carried = capacity_mbps
        latency = base_latency_ms * 50.0
        loss = (utilization - 1.0) / utilization * 100.0
    utilization = min(utilization, 2.0)
    return carried, utilization, latency, loss


@dataclass
class BackgroundFlow:
    """Persistent low-rate cross-traffic on a specific edge."""

    edge: Tuple[str, str]
    rate_mean_mbps: float
    rate_std_mbps: float


class Simulator:
    """Run a scenario under a routing policy and yield per-link records."""

    def __init__(
        self,
        graph: nx.Graph,
        scenario: TrafficScenario,
        policy: RoutingPolicy,
        policy_name: str,
        source: str = "h1",
        destination: str = "h2",
        sim_cfg: Optional[SimulationConfig] = None,
        background_flows: Optional[List[BackgroundFlow]] = None,
        rng: Optional[np.random.Generator] = None,
    ) -> None:
        self.graph = graph
        self.scenario = scenario
        self.policy = policy
        self.policy_name = policy_name
        self.source = source
        self.destination = destination
        self.sim_cfg = sim_cfg or CONFIG.simulation
        self.background_flows = background_flows or self._default_background_flows()
        self.rng = rng or np.random.default_rng(self.sim_cfg.random_seed + 7)

        self._known_paths: List[List[str]] = list(
            nx.all_simple_paths(graph, source=source, target=destination)
        )

    def _default_background_flows(self) -> List[BackgroundFlow]:
        flows: List[BackgroundFlow] = []
        traffic_cfg = CONFIG.traffic
        rng = np.random.default_rng(self.sim_cfg.random_seed + 13)
        switch_edges = [
            edge_key(u, v)
            for u, v, d in self.graph.edges(data=True)
            if self.graph.nodes[u].get("kind") == "switch"
            and self.graph.nodes[v].get("kind") == "switch"
        ]
        if not switch_edges:
            return flows
        for _ in range(traffic_cfg.background_flows):
            edge = switch_edges[int(rng.integers(0, len(switch_edges)))]
            flows.append(
                BackgroundFlow(
                    edge=edge,
                    rate_mean_mbps=float(rng.uniform(4.0, 10.0)),
                    rate_std_mbps=2.0,
                )
            )
        return flows

    def _path_index(self, path: List[str]) -> int:
        for idx, known in enumerate(self._known_paths):
            if known == path:
                return idx
        self._known_paths.append(path)
        return len(self._known_paths) - 1

    def run(self) -> List[TickRecord]:
        records: List[TickRecord] = []
        recent_util: Dict[Tuple[str, str], float] = {
            edge_key(u, v): 0.0 for u, v in self.graph.edges()
        }

        for tick in range(len(self.scenario)):
            offered = float(self.scenario.offered_load_mbps[tick])
            ctx = RoutingContext(
                tick=tick,
                graph=self.graph,
                source=self.source,
                destination=self.destination,
                recent_utilization=dict(recent_util),
                offered_load_mbps=offered,
            )
            decision = _normalize_policy_output(self.policy(ctx))
            path_edge_lists: List[List[Tuple[str, str]]] = [
                list(path_edges(p)) for p, _ in decision
            ]
            chosen_edges = {e for edges in path_edge_lists for e in edges}
            dominant_path = max(decision, key=lambda pw: pw[1])[0]
            path_idx = self._path_index(dominant_path)

            edge_offered: Dict[Tuple[str, str], float] = {
                edge_key(u, v): 0.0 for u, v in self.graph.edges()
            }
            for (path, weight), edges in zip(decision, path_edge_lists):
                for e in edges:
                    edge_offered[e] += offered * weight

            for flow in self.background_flows:
                bg = float(
                    max(
                        0.0,
                        self.rng.normal(flow.rate_mean_mbps, flow.rate_std_mbps),
                    )
                )
                edge_offered[flow.edge] += bg

            per_edge: Dict[
                Tuple[str, str], Tuple[float, float, float, float, float, float]
            ] = {}
            for (u, v, data) in self.graph.edges(data=True):
                e = edge_key(u, v)
                cap = float(data["capacity_mbps"])
                base_lat = float(data["base_latency_ms"])
                off = edge_offered[e]
                carried, util, latency, loss = _link_metrics(off, cap, base_lat)
                recent_util[e] = util
                per_edge[e] = (off, carried, cap, util, latency, loss)

            effective_latency = 0.0
            effective_throughput = 0.0
            effective_loss = 0.0
            for (_, weight), edges in zip(decision, path_edge_lists):
                path_latency = sum(per_edge[e][4] for e in edges)
                path_min_carried = min(per_edge[e][1] for e in edges)
                path_max_loss = max(per_edge[e][5] for e in edges)
                effective_latency += weight * path_latency
                # Each path delivers what its bottleneck allows; the flow's
                # total throughput is the sum across paths it uses.
                effective_throughput += path_min_carried
                effective_loss += weight * path_max_loss

            for e, (off, carried, cap, util, latency, loss) in per_edge.items():
                records.append(
                    TickRecord(
                        tick=tick,
                        scenario=self.scenario.name,
                        policy=self.policy_name,
                        link=e,
                        offered_load_mbps=off,
                        carried_load_mbps=carried,
                        capacity_mbps=cap,
                        utilization=util,
                        latency_ms=latency,
                        packet_loss_pct=loss,
                        path_index=path_idx,
                        is_on_active_path=(e in chosen_edges),
                        effective_latency_ms=effective_latency,
                        effective_throughput_mbps=effective_throughput,
                        effective_packet_loss_pct=effective_loss,
                    )
                )

        return records
