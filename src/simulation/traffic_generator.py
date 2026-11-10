"""Traffic generators for normal and congestion scenarios.

Each generator produces a per-tick offered load in Mbps for the primary
source->destination pair. We deliberately keep the model simple (Gaussian
noise around a mean, with scheduled burst windows) so the LSTM has a learnable
pattern and the white paper's results are reproducible.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

import numpy as np

from src.config import CONFIG, SimulationConfig, TrafficConfig


@dataclass
class TrafficScenario:
    """A single experiment's offered-load time series (Mbps per tick)."""

    name: str
    offered_load_mbps: np.ndarray
    burst_windows: Sequence[tuple[int, int]]

    def __len__(self) -> int:
        return int(self.offered_load_mbps.shape[0])


def _rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)


def normal_scenario(
    sim_cfg: SimulationConfig | None = None,
    traffic_cfg: TrafficConfig | None = None,
    seed_offset: int = 0,
) -> TrafficScenario:
    """Steady moderate traffic, no bursts.

    Useful for training the LSTM on a 'baseline' regime and for the
    `normal_*` evaluation scenarios.
    """
    sim_cfg = sim_cfg or CONFIG.simulation
    traffic_cfg = traffic_cfg or CONFIG.traffic
    rng = _rng(sim_cfg.random_seed + seed_offset)

    load = rng.normal(
        traffic_cfg.normal_mean_mbps,
        traffic_cfg.normal_std_mbps,
        size=sim_cfg.duration_ticks,
    )
    return TrafficScenario(
        name="normal",
        offered_load_mbps=np.clip(load, 0.0, None),
        burst_windows=(),
    )


def congestion_scenario(
    sim_cfg: SimulationConfig | None = None,
    traffic_cfg: TrafficConfig | None = None,
    seed_offset: int = 1,
) -> TrafficScenario:
    """Normal traffic with scheduled high-intensity burst windows.

    Bursts are configured in `TrafficConfig.burst_windows` so the model can
    learn the lead-in pattern (gradual ramp + sustained burst).
    """
    sim_cfg = sim_cfg or CONFIG.simulation
    traffic_cfg = traffic_cfg or CONFIG.traffic
    rng = _rng(sim_cfg.random_seed + seed_offset)

    load = rng.normal(
        traffic_cfg.normal_mean_mbps,
        traffic_cfg.normal_std_mbps,
        size=sim_cfg.duration_ticks,
    )
    for start, end in traffic_cfg.burst_windows:
        if start >= sim_cfg.duration_ticks:
            continue
        end = min(end, sim_cfg.duration_ticks)
        ramp = np.linspace(0.0, 1.0, num=max(1, (end - start) // 8))
        burst_means = np.full(end - start, traffic_cfg.burst_mean_mbps)
        burst_means[: ramp.size] = (
            traffic_cfg.normal_mean_mbps
            + ramp * (traffic_cfg.burst_mean_mbps - traffic_cfg.normal_mean_mbps)
        )
        burst_noise = rng.normal(0.0, traffic_cfg.burst_std_mbps, size=end - start)
        load[start:end] = burst_means + burst_noise

    return TrafficScenario(
        name="congestion",
        offered_load_mbps=np.clip(load, 0.0, None),
        burst_windows=tuple(traffic_cfg.burst_windows),
    )


def mixed_training_scenario(
    sim_cfg: SimulationConfig | None = None,
    traffic_cfg: TrafficConfig | None = None,
) -> TrafficScenario:
    """Long mixed trace combining normal periods and bursts.

    Used to build the LSTM training dataset so it sees both regimes.
    """
    sim_cfg = sim_cfg or CONFIG.simulation
    traffic_cfg = traffic_cfg or CONFIG.traffic
    rng = _rng(sim_cfg.random_seed + 99)

    base = rng.normal(
        traffic_cfg.normal_mean_mbps,
        traffic_cfg.normal_std_mbps,
        size=sim_cfg.duration_ticks,
    )

    n_bursts = max(3, sim_cfg.duration_ticks // 90)
    burst_len = 40
    starts = rng.integers(
        low=20, high=max(21, sim_cfg.duration_ticks - burst_len - 10), size=n_bursts
    )
    windows: List[tuple[int, int]] = []
    for s in sorted(int(x) for x in starts):
        e = min(s + burst_len, sim_cfg.duration_ticks)
        ramp = np.linspace(0.0, 1.0, num=max(1, (e - s) // 6))
        means = np.full(e - s, traffic_cfg.burst_mean_mbps)
        means[: ramp.size] = (
            traffic_cfg.normal_mean_mbps
            + ramp * (traffic_cfg.burst_mean_mbps - traffic_cfg.normal_mean_mbps)
        )
        noise = rng.normal(0.0, traffic_cfg.burst_std_mbps, size=e - s)
        base[s:e] = means + noise
        windows.append((s, e))

    return TrafficScenario(
        name="training_mixed",
        offered_load_mbps=np.clip(base, 0.0, None),
        burst_windows=tuple(windows),
    )
