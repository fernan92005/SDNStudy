"""Central configuration for the AI-driven predictive 6G traffic prototype.

All experiment knobs live here so that scripts and modules stay declarative and
the white paper's methodology section can cite exact values.

Stress-topology variant
-----------------------
Set ``WP_STRESS_TOPOLOGY=1`` (or ``true``/``yes``) in the environment to use
the stress-topology variant: same capacities, but the alternate path's per-hop
base latency is increased from 3 ms to 15 ms. This is the regime where blind
ECMP pays a constant latency tax (half the traffic always crosses the slow
alternate) while the selective predictive router only pays the tax during
congestion bursts -- demonstrating where the AI uniquely wins, rather than
just matching ECMP. Outputs are redirected to ``data_stress/`` / ``results_stress/``
so the baseline-topology artifacts under ``data/`` / ``results/`` are
untouched. See ``docs/methodology.md`` and ``docs/track_b.md`` for the
analytical rationale.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple


def _stress_topology_enabled() -> bool:
    return os.environ.get("WP_STRESS_TOPOLOGY", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


STRESS_TOPOLOGY: bool = _stress_topology_enabled()

PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
MODELS_DIR: Path = PROJECT_ROOT / "models"
DOCS_FIGURES_DIR: Path = PROJECT_ROOT / "docs" / "figures"

if STRESS_TOPOLOGY:
    DATA_DIR: Path = PROJECT_ROOT / "data_stress"
    RESULTS_DIR: Path = PROJECT_ROOT / "results_stress"
else:
    DATA_DIR = PROJECT_ROOT / "data"
    RESULTS_DIR = PROJECT_ROOT / "results"

FIGURES_DIR: Path = RESULTS_DIR / "figures"


@dataclass(frozen=True)
class LinkSpec:
    """Static properties of a physical link in the simulated topology."""

    src: str
    dst: str
    capacity_mbps: float
    base_latency_ms: float


_ALT_LATENCY_MS: float = 15.0 if STRESS_TOPOLOGY else 3.0


@dataclass(frozen=True)
class TopologyConfig:
    """Three-switch multipath topology matching the architecture diagram.

    h1 -- s1 ===(primary)=== s2 -- h2
              \\                  //
               \\=== s3 ========//

    The bottleneck link is intentionally narrower than the alternate path to
    create a realistic congestion scenario when the predictive router engages.

    The alternate-path per-hop base latency is 3 ms in the baseline topology
    and 15 ms under ``WP_STRESS_TOPOLOGY=1`` (see module docstring).
    """

    hosts: Tuple[str, ...] = ("h1", "h2")
    switches: Tuple[str, ...] = ("s1", "s2", "s3")
    links: Tuple[LinkSpec, ...] = (
        LinkSpec("h1", "s1", capacity_mbps=1000.0, base_latency_ms=0.1),
        LinkSpec("s1", "s2", capacity_mbps=100.0, base_latency_ms=2.0),
        LinkSpec("s1", "s3", capacity_mbps=180.0, base_latency_ms=_ALT_LATENCY_MS),
        LinkSpec("s3", "s2", capacity_mbps=180.0, base_latency_ms=_ALT_LATENCY_MS),
        LinkSpec("s2", "h2", capacity_mbps=1000.0, base_latency_ms=0.1),
    )
    primary_path: Tuple[str, ...] = ("h1", "s1", "s2", "h2")
    alternate_path: Tuple[str, ...] = ("h1", "s1", "s3", "s2", "h2")


@dataclass(frozen=True)
class SimulationConfig:
    """Discrete-time simulation parameters."""

    duration_ticks: int = 600
    tick_seconds: float = 1.0
    random_seed: int = 42
    warmup_ticks: int = 20


@dataclass(frozen=True)
class TrafficConfig:
    """Traffic generator parameters for normal and congestion scenarios."""

    normal_mean_mbps: float = 25.0
    normal_std_mbps: float = 6.0

    burst_mean_mbps: float = 120.0
    burst_std_mbps: float = 8.0
    burst_windows: Tuple[Tuple[int, int], ...] = (
        (120, 200),
        (320, 410),
        (500, 560),
    )

    background_flows: int = 3


@dataclass(frozen=True)
class MLConfig:
    """LSTM model + training hyperparameters."""

    feature_columns: Tuple[str, ...] = (
        "throughput_mbps",
        "latency_ms",
        "packet_loss_pct",
        "utilization",
    )
    target_column: str = "utilization"

    window_size: int = 20
    horizon: int = 5

    lstm_units: Tuple[int, int] = (64, 32)
    dropout: float = 0.1
    learning_rate: float = 1e-3
    batch_size: int = 64
    epochs: int = 25
    validation_split: float = 0.2

    train_split: float = 0.7
    val_split: float = 0.15

    congestion_threshold: float = 0.75


@dataclass(frozen=True)
class DecisionConfig:
    """Rule-based decision engine parameters."""

    congestion_threshold: float = 0.75
    hysteresis: float = 0.10
    min_reroute_duration: int = 5


@dataclass(frozen=True)
class Config:
    topology: TopologyConfig = field(default_factory=TopologyConfig)
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    traffic: TrafficConfig = field(default_factory=TrafficConfig)
    ml: MLConfig = field(default_factory=MLConfig)
    decision: DecisionConfig = field(default_factory=DecisionConfig)


CONFIG: Config = Config()


def ensure_dirs() -> None:
    for d in (DATA_DIR, MODELS_DIR, RESULTS_DIR, FIGURES_DIR, DOCS_FIGURES_DIR):
        d.mkdir(parents=True, exist_ok=True)
