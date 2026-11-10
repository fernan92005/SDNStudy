"""AI-driven predictive router.

At every tick the router:

  1. Maintains a rolling window of per-link metrics observed so far.
  2. Asks the LSTM forecaster to predict the next-horizon utilization for
     every link on the default path.
  3. Feeds those forecasts into the rule-based `DecisionEngine`.
  4. If the engine decides to reroute, picks the alternate path with the
     lowest predicted peak utilization.

This emulates an SDN controller that runs an "edge AI agent" co-located with
the source-side switch — the 6G / edge intelligence framing in the paper.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, List, Optional, Sequence, Tuple

import networkx as nx
import numpy as np
import pandas as pd

from src.config import CONFIG
from src.decision.engine import Action, DecisionEngine
from src.ml.predict import LoadedForecaster
from src.simulation.simulator import RoutingContext, RoutingPolicy
from src.simulation.topology import edge_key, path_edges


def _history_to_link_frame(
    link: Tuple[str, str],
    rows: Sequence[Dict[str, float]],
    feature_columns: Sequence[str],
) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    for col in feature_columns:
        if col not in df:
            df[col] = 0.0
    return df


def make_predictive_router(
    source: str,
    destination: str,
    graph: nx.Graph,
    forecaster: LoadedForecaster,
    default_path: Optional[List[str]] = None,
    alternate_paths: Optional[List[List[str]]] = None,
    log: Optional[List[Dict[str, float]]] = None,
) -> RoutingPolicy:
    """Build a stateful routing policy bound to a particular forecaster.

    The returned callable is closure-stateful: it tracks per-link history and
    the engine's previous decision so it can apply hysteresis correctly.
    """
    default_path = default_path or nx.shortest_path(
        graph, source=source, target=destination, weight="weight"
    )

    enumerated = list(nx.all_simple_paths(graph, source=source, target=destination))
    if alternate_paths is None:
        alternate_paths = [p for p in enumerated if p != default_path]

    engine = DecisionEngine()
    feature_columns = forecaster.feature_columns
    history: Dict[Tuple[str, str], Deque[Dict[str, float]]] = defaultdict(
        lambda: deque(maxlen=forecaster.window_size + 5)
    )

    def _record_observation(ctx: RoutingContext) -> None:
        for (u, v, data) in ctx.graph.edges(data=True):
            e = edge_key(u, v)
            util = ctx.recent_utilization.get(e, 0.0)
            row = {
                "throughput_mbps": util * float(data["capacity_mbps"]),
                "latency_ms": float(data["base_latency_ms"])
                * (1.0 / max(1e-3, 1.0 - min(util, 0.999))),
                "packet_loss_pct": max(0.0, (util - 1.0) / max(util, 1e-6) * 100.0),
                "utilization": util,
            }
            history[e].append(row)

    per_tick_cache: Dict[Tuple[int, Tuple[str, str]], Optional[np.ndarray]] = {}

    def _forecast_edge(tick: int, e: Tuple[str, str]) -> Optional[np.ndarray]:
        key = (tick, e)
        if key in per_tick_cache:
            return per_tick_cache[key]
        df = _history_to_link_frame(e, list(history[e]), feature_columns)
        f = forecaster.predict_link(df) if not df.empty else None
        per_tick_cache[key] = f
        return f

    def _forecast_for_path(
        tick: int, path: List[str]
    ) -> List[Optional[np.ndarray]]:
        return [_forecast_edge(tick, e) for e in path_edges(path)]

    def policy(ctx: RoutingContext) -> List[str]:
        _record_observation(ctx)

        if len(per_tick_cache) > 256:
            per_tick_cache.clear()

        default_forecasts = _forecast_for_path(ctx.tick, default_path)
        decision = engine.step(default_forecasts)

        chosen: List[str] = default_path
        chosen_reason = decision.reason
        if decision.action == Action.REROUTE and alternate_paths:
            best_peak = float("inf")
            best_path = default_path
            for path in alternate_paths:
                peaks = _forecast_for_path(ctx.tick, path)
                peak = max(
                    (float(np.max(p)) for p in peaks if p is not None), default=1e9
                )
                if peak < best_peak:
                    best_peak = peak
                    best_path = path
            chosen = best_path
            chosen_reason = (
                f"reroute -> {chosen} (predicted peak {best_peak:.2f})"
            )

        if log is not None:
            log.append(
                {
                    "tick": ctx.tick,
                    "action": decision.action.value,
                    "predicted_peak": float(decision.predicted_peak_utilization),
                    "chosen_path": "->".join(chosen),
                    "reason": chosen_reason,
                }
            )
        return chosen

    policy.__name__ = "predictive_router"
    return policy
