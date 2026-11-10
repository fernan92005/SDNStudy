"""NetworkX topology builder.

We model the network as an undirected weighted graph where each edge stores
its static capacity (Mbps) and base propagation latency (ms). The graph is
used by both static (shortest-path) and predictive routers, and by the
simulator to track per-link load over time.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

import networkx as nx

from src.config import CONFIG, LinkSpec, TopologyConfig


def build_topology(topology_cfg: TopologyConfig | None = None) -> nx.Graph:
    """Build the multipath topology described in `config.TopologyConfig`."""
    topology_cfg = topology_cfg or CONFIG.topology
    g = nx.Graph()

    for node in (*topology_cfg.hosts, *topology_cfg.switches):
        kind = "host" if node in topology_cfg.hosts else "switch"
        g.add_node(node, kind=kind)

    for link in topology_cfg.links:
        g.add_edge(
            link.src,
            link.dst,
            capacity_mbps=link.capacity_mbps,
            base_latency_ms=link.base_latency_ms,
            weight=link.base_latency_ms,
        )

    return g


def edge_key(u: str, v: str) -> Tuple[str, str]:
    """Canonical undirected edge identifier (sorted endpoints)."""
    return tuple(sorted((u, v)))  # type: ignore[return-value]


def path_edges(path: List[str]) -> List[Tuple[str, str]]:
    """Convert a node-sequence path to a list of canonical edge keys."""
    return [edge_key(path[i], path[i + 1]) for i in range(len(path) - 1)]


def all_simple_paths(
    g: nx.Graph, source: str, target: str, cutoff: int | None = None
) -> List[List[str]]:
    """Enumerate all simple paths between two endpoints."""
    return list(nx.all_simple_paths(g, source=source, target=target, cutoff=cutoff))


def link_capacities(g: nx.Graph) -> Dict[Tuple[str, str], float]:
    return {edge_key(u, v): d["capacity_mbps"] for u, v, d in g.edges(data=True)}


def link_base_latencies(g: nx.Graph) -> Dict[Tuple[str, str], float]:
    return {edge_key(u, v): d["base_latency_ms"] for u, v, d in g.edges(data=True)}


def summarize(g: nx.Graph) -> str:
    parts = [
        f"nodes={g.number_of_nodes()}",
        f"edges={g.number_of_edges()}",
    ]
    for u, v, d in g.edges(data=True):
        parts.append(
            f"  {u}-{v}: cap={d['capacity_mbps']:.0f}Mbps "
            f"base_latency={d['base_latency_ms']:.1f}ms"
        )
    return "\n".join(parts)
