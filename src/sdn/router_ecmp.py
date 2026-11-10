"""ECMP (Equal-Cost Multi-Path) baseline router.

ECMP is the standard "split traffic across all equal-cost shortest paths"
policy used in production data-center fabrics and many ISP backbones. It is
*not* AI-driven: the split is computed once from the topology and applied
uniformly to all traffic, regardless of current load.

In a real router, ECMP hashes each flow's 5-tuple to one of the equal-cost
next hops, so the long-run distribution across N paths is ~1/N per path.
We model this *at the fluid level* by simultaneously sending a fraction
``1/N`` of the offered load down each of the N paths at every tick. This
matches the steady-state behaviour of hash-based ECMP with many flows,
which is the regime production networks actually operate in.

ECMP is a strong baseline because:

1. It is the de-facto industry standard for static multipath load balancing.
2. It uses both paths, so it cannot be beaten just by "discovering" that
   an alternate path exists - the predictive router has to actually pick
   the *right* path at the right time.

Returning ``[(primary, w), (alternate, w), ...]`` exercises the simulator's
weighted-multipath code path; single-path routers can keep returning a
plain ``List[str]``.
"""
from __future__ import annotations

from typing import List, Tuple

import networkx as nx

from src.simulation.simulator import RoutingContext, RoutingPolicy


def make_ecmp_router(
    source: str,
    destination: str,
    graph: nx.Graph,
    max_paths: int = 2,
) -> RoutingPolicy:
    """Build an ECMP-style router that splits load equally across the
    ``max_paths`` shortest simple paths between source and destination.
    """
    all_paths: List[List[str]] = list(
        nx.shortest_simple_paths(graph, source=source, target=destination, weight="weight")
    )
    if not all_paths:
        raise ValueError(f"No path from {source!r} to {destination!r}")

    selected: List[List[str]] = all_paths[: max(1, max_paths)]
    weight = 1.0 / len(selected)
    decision: List[Tuple[List[str], float]] = [(p, weight) for p in selected]

    def policy(ctx: RoutingContext):
        return decision

    policy.__name__ = "ecmp_router"
    return policy
