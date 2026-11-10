"""Baseline static router — shortest path by base latency.

This is the 'WITHOUT AI' control. The chosen path is decided once and used
for every tick, regardless of the current network state.
"""
from __future__ import annotations

from typing import List

import networkx as nx

from src.simulation.simulator import RoutingContext, RoutingPolicy


def make_static_router(source: str, destination: str, graph: nx.Graph) -> RoutingPolicy:
    fixed_path: List[str] = nx.shortest_path(
        graph, source=source, target=destination, weight="weight"
    )

    def policy(ctx: RoutingContext) -> List[str]:
        return fixed_path

    policy.__name__ = "static_router"
    return policy
