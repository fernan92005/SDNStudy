"""Poll OVS port counters and emit Track A-compatible CSV.

Runs on the WSL host (it needs `ovs-vsctl` / `ovs-ofctl` on PATH, but does NOT
need to be inside any Mininet namespace).

    python3 mininet/collect_metrics.py \
        --scenario congestion --policy static \
        --duration 600 --interval 1.0 \
        --out data/congestion_static.csv

The output CSV matches `src/data/collector.py`'s schema (all 18 columns
including `is_on_active_path` and the three `effective_*` columns) so the
rest of the Track A pipeline (`scripts/05_evaluate.py`) consumes it
unchanged.

Inputs (written by sibling Track B processes):

- `mininet/runtime.json` -- written by `topology.py` on startup. Contains
  the per-link OVS interface map, switch dpids, host PIDs/IPs.
- `mininet/runtime/active_path.json` -- written every STATS_INTERVAL by
  `ryu_app.py`. Records which path the controller is currently steering
  traffic onto: primary `h1-s1-s2-h2` or alternate `h1-s1-s3-s2-h2`. If
  missing or stale, the collector defaults to the primary path (this is
  the static-mode behaviour).
- `mininet/runtime/ping.log` -- written by `traffic_iperf.sh ping`. Per
  tick we extract the median RTT from samples that arrived during that
  tick window and store it as `effective_latency_ms`.

Each tick the collector emits one row per *physical link* in the
topology (5 links, both directions are NOT enumerated -- we report one
row per link in its declared src->dst direction matching Track A).
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import shlex
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

MININET_DIR = Path(__file__).resolve().parent
RUNTIME_JSON = MININET_DIR / "runtime.json"
RUNTIME_DIR = MININET_DIR / "runtime"
ACTIVE_PATH_JSON = RUNTIME_DIR / "active_path.json"
PING_LOG = RUNTIME_DIR / "ping.log"

# Per-link declaration, src->dst direction the row reports on. For inter-
# switch links we read the egress (tx) counters on the src switch's port
# facing dst. For host-facing links (h1-s1, s2-h2) we measure on the
# switch side using the direction that matches the dominant flow:
#   - h1->s1: data ingressing s1 from h1  (read s1's rx counters on the
#     port facing h1).
#   - s2->h2: data egressing s2 toward h2 (read s2's tx counters on the
#     port facing h2).
# This matches the per-edge orientation used by Track A's TickRecord
# (carried_load is read in the src->dst direction).
LINKS: Tuple[Dict[str, Any], ...] = (
    {"src": "h1", "dst": "s1", "ovs_switch": "s1", "facing": "h1", "direction": "rx", "capacity_mbps": 1000.0},
    {"src": "s1", "dst": "s2", "ovs_switch": "s1", "facing": "s2", "direction": "tx", "capacity_mbps":  100.0},
    {"src": "s1", "dst": "s3", "ovs_switch": "s1", "facing": "s3", "direction": "tx", "capacity_mbps":  180.0},
    {"src": "s3", "dst": "s2", "ovs_switch": "s3", "facing": "s2", "direction": "tx", "capacity_mbps":  180.0},
    {"src": "s2", "dst": "h2", "ovs_switch": "s2", "facing": "h2", "direction": "tx", "capacity_mbps": 1000.0},
)

PRIMARY_PATH_LINKS = {("h1", "s1"), ("s1", "s2"), ("s2", "h2")}
ALTERNATE_PATH_LINKS = {("h1", "s1"), ("s1", "s3"), ("s3", "s2"), ("s2", "h2")}

# `ovs-ofctl dump-ports` output, OF1.3 format. Example two-line block:
#   port  2: rx pkts=12, bytes=896, drop=0, errs=0, frame=0, over=0, crc=0
#            tx pkts=14, bytes=1024, drop=0, errs=0, coll=0
_PORT_HEADER = re.compile(
    r"^\s*port\s+\"?(?P<port>[^\":]+)\"?:\s*"
    r"rx pkts=(?P<rx_pkts>\d+),\s*bytes=(?P<rx_bytes>\d+),\s*drop=(?P<rx_drop>\d+)"
)
_TX_LINE = re.compile(
    r"^\s*tx pkts=(?P<tx_pkts>\d+),\s*bytes=(?P<tx_bytes>\d+),\s*drop=(?P<tx_drop>\d+)"
)

# `[1734567890.123456] 64 bytes from 10.0.0.2: icmp_seq=42 ttl=64 time=2.45 ms`
_PING_LINE = re.compile(
    r"^\[(?P<ts>\d+\.\d+)\]\s+\d+\s+bytes from .* time=(?P<rtt>[\d.]+)\s*ms"
)


def _run(cmd: str) -> str:
    # Capture stderr so the caller can show a meaningful error when ovs-ofctl
    # fails (most common failure: missing sudo => exit 1 => silent zeros).
    return subprocess.check_output(
        shlex.split(cmd), text=True, stderr=subprocess.PIPE
    )


_OVS_PERMS_HINT = (
    "ovs-ofctl needs root to read OVS state. Re-run the collector with sudo:\n"
    "    sudo python3 mininet/collect_metrics.py ..."
)


def _preflight_ovs_access(switches: List[str]) -> None:
    """Try one dump-ports call up-front so we fail loudly (not silently with
    zeros) if ovs-ofctl can't read OVS -- almost always missing sudo.
    """
    for sw in switches:
        try:
            _run(f"ovs-ofctl -O OpenFlow13 dump-ports {sw}")
            return
        except FileNotFoundError as exc:
            raise SystemExit(f"ovs-ofctl not on PATH: {exc}")
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise SystemExit(
                f"ovs-ofctl dump-ports {sw} failed (exit {exc.returncode}).\n"
                f"  stderr: {stderr or '<empty -- usually means permission denied>'}\n\n"
                f"{_OVS_PERMS_HINT}"
            )


def _dump_ports(switch: str) -> Dict[str, Dict[str, int]]:
    """Return {port_name_or_number: {rx_bytes, rx_pkts, rx_drop, tx_bytes,
    tx_pkts, tx_drop}} for one switch.
    """
    out = _run(f"ovs-ofctl -O OpenFlow13 dump-ports {switch}")
    stats: Dict[str, Dict[str, int]] = {}
    current: Optional[str] = None
    pending: Dict[str, int] = {}
    for line in out.splitlines():
        m = _PORT_HEADER.match(line)
        if m:
            if current is not None:
                stats[current] = pending
            current = m.group("port").strip()
            pending = {
                "rx_pkts":  int(m.group("rx_pkts")),
                "rx_bytes": int(m.group("rx_bytes")),
                "rx_drop":  int(m.group("rx_drop")),
                "tx_pkts":  0, "tx_bytes": 0, "tx_drop": 0,
            }
            continue
        m = _TX_LINE.match(line)
        if m and current is not None:
            pending["tx_pkts"]  = int(m.group("tx_pkts"))
            pending["tx_bytes"] = int(m.group("tx_bytes"))
            pending["tx_drop"]  = int(m.group("tx_drop"))
    if current is not None:
        stats[current] = pending
    return stats


def _load_runtime() -> Dict[str, Any]:
    if not RUNTIME_JSON.exists():
        raise SystemExit(
            f"{RUNTIME_JSON} not found. Bring the topology up first:\n"
            f"    sudo .venv/bin/python mininet/topology.py"
        )
    return json.loads(RUNTIME_JSON.read_text())


def _resolve_port_numbers(runtime: Dict[str, Any]) -> Dict[Tuple[str, str], Tuple[str, int]]:
    """For each (ovs_switch, facing_node) we use, return (interface_name, of_port_no).

    Pulls from runtime.json's `port_map` + `switch_port_map`, both written
    by `topology.py` after `net.start()`. Doing this here (rather than
    parsing `dump-ports-desc`) keeps the mapping deterministic against any
    future reorderings of addLink() calls.
    """
    port_map = runtime.get("port_map", {})
    switch_port_map = runtime.get("switch_port_map", {})
    resolved: Dict[Tuple[str, str], Tuple[str, int]] = {}
    for link in LINKS:
        sw = link["ovs_switch"]
        facing = link["facing"]
        key = f"{sw}->{facing}"
        entry = port_map.get(key)
        if entry is None:
            raise SystemExit(
                f"No port_map entry for {key} in {RUNTIME_JSON}. Re-run topology.py."
            )
        intf_name = entry["src_intf"]
        port_no = switch_port_map.get(sw, {}).get(intf_name)
        if port_no is None:
            raise SystemExit(
                f"No OF port number for interface {intf_name} on {sw} in runtime.json."
            )
        resolved[(sw, facing)] = (intf_name, int(port_no))
    return resolved


def _read_active_path(now_epoch: float, max_age_s: float = 10.0) -> Tuple[str, List[str]]:
    """Return (mode, path) from ryu_app's published active_path.json.

    Falls back to ("static", primary_path) when the file is missing/stale
    -- which is the correct behaviour for MODE=static (controller never
    writes the file) and for the brief startup window in predictive mode.
    """
    primary = ["h1", "s1", "s2", "h2"]
    if not ACTIVE_PATH_JSON.exists():
        return ("static", primary)
    try:
        data = json.loads(ACTIVE_PATH_JSON.read_text())
        if now_epoch - float(data.get("ts_epoch", 0.0)) > max_age_s:
            return ("static", primary)
        path = list(data.get("path") or primary)
        mode = str(data.get("mode") or "static")
        return (mode, path)
    except (json.JSONDecodeError, OSError, ValueError):
        return ("static", primary)


def _read_ping_window(ping_file: Path, t_lo: float, t_hi: float) -> Optional[float]:
    """Return the median RTT (ms) from ping samples whose timestamp falls
    in [t_lo, t_hi). Returns None if no samples landed in the window.

    We re-read the whole file each tick. It grows at ~1 line per second so
    this stays cheap for the 600 s experiment window.
    """
    if not ping_file.exists():
        return None
    try:
        text = ping_file.read_text(errors="ignore")
    except OSError:
        return None
    samples: List[float] = []
    for line in text.splitlines():
        m = _PING_LINE.match(line)
        if not m:
            continue
        ts = float(m.group("ts"))
        if t_lo <= ts < t_hi:
            samples.append(float(m.group("rtt")))
    if not samples:
        return None
    return float(statistics.median(samples))


def collect(
    out_csv: Path,
    duration: float,
    interval: float,
    scenario: str,
    policy: str,
) -> None:
    runtime = _load_runtime()
    resolved = _resolve_port_numbers(runtime)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    switches = sorted({link["ovs_switch"] for link in LINKS})
    _preflight_ovs_access(switches)
    print(f"[collect_metrics] ovs-ofctl ok on switches {switches}; "
          f"writing CSV every {interval:g}s to {out_csv}", flush=True)

    fieldnames = [
        "tick", "scenario", "policy", "link", "src", "dst",
        "offered_load_mbps", "carried_load_mbps", "capacity_mbps", "utilization",
        "latency_ms", "packet_loss_pct", "throughput_mbps",
        "path_index", "is_on_active_path",
        "effective_latency_ms", "effective_throughput_mbps", "effective_packet_loss_pct",
        "ts_epoch",  # [informe] para alinear con el log del controlador y con iperf3
    ]

    with out_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()

        # Warm-start the deltas so the first tick is not a huge spike.
        # OVS may key dump-ports output by interface name ("s1-eth2") on
        # newer versions or by OF port number ("2") on older ones; try
        # both so the collector works across versions.
        zero_counters = {
            "rx_bytes": 0, "rx_pkts": 0, "rx_drop": 0,
            "tx_bytes": 0, "tx_pkts": 0, "tx_drop": 0,
        }
        prev: Dict[str, Dict[str, int]] = {}
        for link in LINKS:
            sw = link["ovs_switch"]
            try:
                sw_stats = _dump_ports(sw)
            except subprocess.CalledProcessError as exc:
                sys.stderr.write(
                    f"[collect_metrics] warm-start: ovs-ofctl dump-ports {sw} "
                    f"failed (exit {exc.returncode}): "
                    f"{(exc.stderr or '').strip()}\n"
                )
                sw_stats = {}
            intf_name, port_no = resolved[(sw, link["facing"])]
            prev[link["src"] + "->" + link["dst"]] = (
                sw_stats.get(intf_name)
                or sw_stats.get(str(port_no))
                or dict(zero_counters)
            )

        start_mono = time.monotonic()
        tick = 0
        while time.monotonic() - start_mono < duration:
            tick_t_lo = time.time()
            tick_t_hi = tick_t_lo + interval

            # Pull fresh OVS stats for each switch we touch.
            per_switch_stats: Dict[str, Dict[str, Dict[str, int]]] = {}
            for sw in {link["ovs_switch"] for link in LINKS}:
                try:
                    per_switch_stats[sw] = _dump_ports(sw)
                except subprocess.CalledProcessError as exc:
                    sys.stderr.write(
                        f"[collect_metrics] tick {tick}: ovs-ofctl dump-ports "
                        f"{sw} failed (exit {exc.returncode}): "
                        f"{(exc.stderr or '').strip()}\n"
                    )
                    per_switch_stats[sw] = {}

            mode, active_path = _read_active_path(now_epoch=tick_t_lo)
            active_pair_set = _path_to_pairs(active_path)
            # path_index: 0 = primary, 1 = alternate. Matches Track A's
            # router_predictive.py convention.
            path_index = 1 if active_pair_set == ALTERNATE_PATH_LINKS else 0

            # End-to-end metrics derived once per tick, used for the
            # `effective_*` columns. We use the median RTT samples that
            # arrived in this tick window for latency and the bottleneck
            # link's throughput / loss for the path-level signals.
            eff_latency_ms = _read_ping_window(PING_LOG, tick_t_lo - interval, tick_t_hi)

            tick_rows: List[Dict[str, Any]] = []
            bottleneck_throughput_mbps = float("inf")
            worst_loss_pct = 0.0

            for link in LINKS:
                key = f"{link['src']}->{link['dst']}"
                sw = link["ovs_switch"]
                intf_name, port_no = resolved[(sw, link["facing"])]
                sw_stats = per_switch_stats.get(sw, {})
                cur = (
                    sw_stats.get(intf_name)
                    or sw_stats.get(str(port_no))
                    or dict(zero_counters)
                )
                prev_link = prev.get(key, cur)

                direction = link["direction"]
                bytes_now = cur[f"{direction}_bytes"]
                bytes_prev = prev_link.get(f"{direction}_bytes", bytes_now)
                pkts_now = cur[f"{direction}_pkts"]
                pkts_prev = prev_link.get(f"{direction}_pkts", pkts_now)
                drop_now = cur[f"{direction}_drop"]
                drop_prev = prev_link.get(f"{direction}_drop", drop_now)

                d_bytes = max(0, bytes_now - bytes_prev)
                d_pkts  = max(0, pkts_now - pkts_prev)
                d_drop  = max(0, drop_now - drop_prev)

                throughput_mbps = (d_bytes * 8.0) / (interval * 1_000_000.0)
                attempted = d_pkts + d_drop
                loss_pct = (d_drop / attempted * 100.0) if attempted > 0 else 0.0
                util = throughput_mbps / link["capacity_mbps"] if link["capacity_mbps"] > 0 else 0.0
                util = min(util, 2.0)  # cap matches simulator's reporting

                on_active = (link["src"], link["dst"]) in active_pair_set
                row = {
                    "tick": tick,
                    "ts_epoch": f"{tick_t_lo:.3f}",
                    "scenario": scenario,
                    "policy": policy,
                    "link": f"{link['src']}-{link['dst']}",
                    "src": link["src"],
                    "dst": link["dst"],
                    "offered_load_mbps": throughput_mbps,   # measured tx ~= offered for UDP
                    "carried_load_mbps": throughput_mbps,
                    "capacity_mbps":     link["capacity_mbps"],
                    "utilization":       util,
                    "latency_ms":        0.0,               # see effective_latency_ms
                    "packet_loss_pct":   loss_pct,
                    "throughput_mbps":   throughput_mbps,
                    "path_index":        path_index,
                    "is_on_active_path": on_active,
                }
                tick_rows.append(row)
                prev[key] = cur

                if on_active:
                    if throughput_mbps > 0 and throughput_mbps < bottleneck_throughput_mbps:
                        bottleneck_throughput_mbps = throughput_mbps
                    if loss_pct > worst_loss_pct:
                        worst_loss_pct = loss_pct

            if bottleneck_throughput_mbps == float("inf"):
                bottleneck_throughput_mbps = 0.0
            # Effective latency: end-to-end ping median. If no ping samples
            # landed in this tick (e.g. ping has not started yet), fall
            # back to 0 so the evaluator's "if eff_lat.max() > 0" check
            # leaves it alone.
            eff_lat_for_csv = float(eff_latency_ms) if eff_latency_ms is not None else 0.0

            for row in tick_rows:
                row["effective_latency_ms"]        = eff_lat_for_csv
                row["effective_throughput_mbps"]   = float(bottleneck_throughput_mbps)
                row["effective_packet_loss_pct"]   = float(worst_loss_pct)
                writer.writerow(row)
            fh.flush()

            tick += 1
            # Sleep precisely so ticks stay 1s apart even if dump-ports drifts.
            sleep_for = max(0.0, tick_t_hi - time.time())
            if sleep_for > 0:
                time.sleep(sleep_for)


def _path_to_pairs(path: List[str]) -> set:
    return {(path[i], path[i + 1]) for i in range(len(path) - 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--out", default="data/mininet_run.csv", type=Path,
                        help="Output CSV path (default: data/mininet_run.csv)")
    parser.add_argument("--duration", type=float, default=600.0,
                        help="Total collection window in seconds (default: 600)")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Polling interval in seconds (default: 1.0; 1 tick = 1s like Track A)")
    parser.add_argument("--scenario", required=True, choices=("normal", "congestion"),
                        help="Stamped on every row; must match traffic_iperf.sh client mode")
    parser.add_argument("--policy", required=True, choices=("static", "predictive", "ecmp"),
                        help="Stamped on every row; matches MODE= the Ryu app was started with")
    args = parser.parse_args()

    try:
        collect(args.out, args.duration, args.interval, args.scenario, args.policy)
    except FileNotFoundError as exc:
        sys.stderr.write(f"OVS tools not found on PATH: {exc}\n")
        sys.exit(1)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
