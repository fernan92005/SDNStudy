"""Rule-based decision engine.

Given LSTM forecasts (or instantaneous utilization), decide whether the
current path should be kept or whether traffic should be rerouted onto an
alternate path. Hysteresis prevents flapping between paths.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Optional

import numpy as np

from src.config import CONFIG, DecisionConfig


class Action(str, Enum):
    KEEP = "keep_default_route"
    REROUTE = "reroute_traffic"


@dataclass
class DecisionState:
    last_action: Action = Action.KEEP
    ticks_since_change: int = 0


@dataclass
class Decision:
    action: Action
    reason: str
    predicted_peak_utilization: float


class DecisionEngine:
    """Apply the rule:

      IF predicted utilization on the current path >= threshold:
          REROUTE
      ELIF currently rerouted AND predicted utilization < threshold - hysteresis
           AND min reroute duration satisfied:
          KEEP (return to default path)
      ELSE:
          maintain previous action
    """

    def __init__(self, decision_cfg: Optional[DecisionConfig] = None) -> None:
        self.cfg = decision_cfg or CONFIG.decision
        self.state = DecisionState()

    def step(
        self,
        forecasts_on_default_path: Iterable[Optional[np.ndarray]],
    ) -> Decision:
        peak = _peak(forecasts_on_default_path)
        self.state.ticks_since_change += 1

        if self.state.last_action == Action.KEEP:
            if peak >= self.cfg.congestion_threshold:
                self.state.last_action = Action.REROUTE
                self.state.ticks_since_change = 0
                return Decision(
                    Action.REROUTE,
                    f"forecast peak {peak:.2f} >= threshold "
                    f"{self.cfg.congestion_threshold:.2f}",
                    peak,
                )
            return Decision(Action.KEEP, "below threshold", peak)

        if (
            peak < self.cfg.congestion_threshold - self.cfg.hysteresis
            and self.state.ticks_since_change >= self.cfg.min_reroute_duration
        ):
            self.state.last_action = Action.KEEP
            self.state.ticks_since_change = 0
            return Decision(
                Action.KEEP,
                f"forecast peak {peak:.2f} cleared hysteresis band",
                peak,
            )
        return Decision(Action.REROUTE, "still above clear-threshold", peak)


def _peak(forecasts: Iterable[Optional[np.ndarray]]) -> float:
    best = 0.0
    for f in forecasts:
        if f is None:
            continue
        v = float(np.max(f))
        if v > best:
            best = v
    return best
