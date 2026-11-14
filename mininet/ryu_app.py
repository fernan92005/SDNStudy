"""OpenFlow controller for Track B (os-ken / Ryu-compatible).

Originally written against Ryu, but Ryu 4.34 does not build on Python
3.12 (it imports the removed ``distutils`` module and uses setuptools
APIs that were dropped in setuptools 58). We use **os-ken**, the
OpenStack Neutron-maintained Ryu fork, which keeps the same module
layout (``os_ken.base.app_manager``, ``os_ken.controller.ofp_event``,
...). The only API difference we hit is that the base class is renamed
``RyuApp -> OSKenApp``.

Two routing modes (selected via the ``MODE`` environment variable):

* ``MODE=static``     - install shortest-path IPv4 flows once at topology
                        discovery and leave them. Equivalent to Track A's
                        ``src/sdn/router_static.py``.

* ``MODE=predictive`` - every ``STATS_INTERVAL`` seconds:

    1. Sweep ``OFPPortStatsRequest`` across switches and compute per-link
       throughput / utilisation / loss deltas.
    2. Feed the rolling 20-tick window for the primary bottleneck link
       (``s1-s2``) into Track A's ``LoadedForecaster.predict_link(df)``.
    3. Pass the horizon forecast to ``DecisionEngine.step([forecast])``;
       if the engine returns ``REROUTE``, swap traffic onto the alternate
       path ``h1-s1-s3-s2-h2``, otherwise keep the primary
       ``h1-s1-s2-h2``.
    4. Re-install IPv4 ``OFPFlowMod`` entries along the chosen path on
       every switch (only when the path actually changes -- we don't
       churn flows every tick).

The trained model + scalers come straight from Track A's ``models/``
directory; we never retrain in Track B (handoff section 2: "Reuse it.").

The controller also writes ``mininet/runtime/active_path.json`` once per
tick. ``collect_metrics.py`` reads that file to stamp the
``is_on_active_path`` column on each CSV row, so the Track A evaluator
attributes latency/loss/throughput to the correct path edges.

Launch with ``mininet/run_controller.py`` (a thin shim around
``os_ken.base.app_manager.AppManager.run_apps``) -- there is no
``ryu-manager`` equivalent shipped with os-ken.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

try:
    from os_ken.base import app_manager
    from os_ken.controller import ofp_event
    from os_ken.controller.handler import (
        CONFIG_DISPATCHER,
        MAIN_DISPATCHER,
        set_ev_cls,
    )
    from os_ken.lib import hub
    from os_ken.lib.packet import ethernet, packet
    from os_ken.ofproto import ofproto_v1_3
    from os_ken.topology import event as topo_event
    from os_ken.topology.api import get_link, get_switch
except ImportError:  # pragma: no cover - only triggered without os-ken installed
    app_manager = object  # type: ignore[assignment]

    class _Missing:
        def __getattr__(self, _name):
            raise ImportError(
                "os-ken is not installed. See mininet/README.md "
                "(create .venv-ryu and run `pip install os-ken`)."
            )

    ofproto_v1_3 = _Missing()  # type: ignore[assignment]


MODE = os.environ.get("MODE", "static").lower()
STATS_INTERVAL = float(os.environ.get("STATS_INTERVAL", "1.0"))
WINDOW_SIZE = 20  # must match CONFIG.ml.window_size
# [informe] FORECASTER=lstm (por defecto) o persistence (referencia trivial:
# repite la ultima utilizacion medida en los 5 pasos del horizonte).
FORECASTER = os.environ.get("FORECASTER", "lstm").lower()

MININET_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = MININET_DIR.parent
RUNTIME_JSON = MININET_DIR / "runtime.json"
RUNTIME_DIR = MININET_DIR / "runtime"
ACTIVE_PATH_JSON = RUNTIME_DIR / "active_path.json"

PRIMARY_PATH = ["h1", "s1", "s2", "h2"]
ALTERNATE_PATH = ["h1", "s1", "s3", "s2", "h2"]
PRIMARY_BOTTLENECK_LINK = "s1-s2"

# Per-link base latency mirrors src/config.py LinkSpec entries; we keep a
# local copy so the controller doesn't need to import the Track A config
# (which would pull TensorFlow into the Ryu venv just to read a dataclass).
BASE_LATENCY_MS: Dict[str, float] = {
    "h1-s1": 0.1, "s2-h2": 0.1,
    "s1-s2": 2.0,
    "s1-s3": 3.0, "s3-s2": 3.0,
}


class PredictiveRouter(app_manager.OSKenApp):  # type: ignore[misc, valid-type]
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.topology_ready = False
        self.datapaths: Dict[str, Any] = {}
        self.dpid_to_name: Dict[int, str] = {}
        self.runtime: Dict[str, Any] = {}
        self.mac_to_port: Dict[int, Dict[str, int]] = {}

        # Rolling per-link history (last WINDOW_SIZE ticks). Keys are
        # "src-dst" strings; rows match Track A's feature_columns.
        self.history: Dict[str, Deque[Dict[str, float]]] = {}
        self.last_counters: Dict[Tuple[str, int], Dict[str, int]] = {}

        self.active_path: List[str] = list(PRIMARY_PATH)
        self.reroutes_total: int = 0
        self.tick: int = 0

        # Forecaster + decision engine -- lazy import in predictive mode so
        # MODE=static keeps the Ryu venv free of TensorFlow.
        self._forecaster = None
        self._engine = None
        self._Action = None
        if MODE == "predictive":
            self._load_predictive_stack()

        self._load_runtime()
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        self.monitor_thread = hub.spawn(self._monitor)

    # ---- bootstrap helpers -------------------------------------------------

    def _load_predictive_stack(self) -> None:
        """Import Track A's forecaster + decision engine.

        Done lazily so static-mode Ryu doesn't pay the TensorFlow cost,
        and so a syntax error in Track A doesn't crash the controller in
        static mode either.
        """
        try:
            if str(PROJECT_ROOT) not in sys.path:
                sys.path.insert(0, str(PROJECT_ROOT))
            from src.decision.engine import Action, DecisionEngine  # type: ignore
            from src.ml.predict import load_forecaster  # type: ignore

            self._forecaster = load_forecaster()
            self._engine = DecisionEngine()
            self._Action = Action
            self.logger.info(
                "Predictive mode ready: forecaster window=%d horizon=%d.",
                self._forecaster.window_size, self._forecaster.horizon,
            )
        except Exception as exc:  # pragma: no cover -- env-dependent
            self.logger.warning(
                "Could not load predictive stack (%s); falling back to static.",
                exc,
            )
            self._forecaster = None
            self._engine = None

    def _load_runtime(self) -> None:
        if not RUNTIME_JSON.exists():
            self.logger.info("runtime.json not present yet; will re-check.")
            return
        try:
            self.runtime = json.loads(RUNTIME_JSON.read_text())
            self.dpid_to_name = {
                int(v): k for k, v in self.runtime.get("dpids", {}).items()
            }
            self.logger.info(
                "Loaded runtime.json: switches=%s dpids=%s",
                self.runtime.get("switches"),
                self.dpid_to_name,
            )
        except (json.JSONDecodeError, OSError) as exc:
            self.logger.warning("Could not parse runtime.json: %s", exc)

    # ---- OF event handlers -------------------------------------------------

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _switch_features(self, ev):
        datapath = ev.msg.datapath
        parser = datapath.ofproto_parser
        ofp = datapath.ofproto

        dpid = datapath.id
        name = self.dpid_to_name.get(dpid)
        if name is not None:
            self.datapaths[name] = datapath

        # Lowest-priority catchall: send unknown packets to the controller
        # for L2 learning (handles ARP). High-priority IPv4 routing flows
        # installed by _install_path() override this.
        match = parser.OFPMatch()
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        self._add_flow(datapath, 0, match, actions)

    def _add_flow(self, datapath, priority, match, actions):
        parser = datapath.ofproto_parser
        ofp = datapath.ofproto
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(
            datapath=datapath, priority=priority, match=match, instructions=inst,
        )
        datapath.send_msg(mod)

    def _del_flows(self, datapath, priority, match):
        parser = datapath.ofproto_parser
        ofp = datapath.ofproto
        mod = parser.OFPFlowMod(
            datapath=datapath, command=ofp.OFPFC_DELETE,
            priority=priority, match=match,
            out_port=ofp.OFPP_ANY, out_group=ofp.OFPG_ANY,
        )
        datapath.send_msg(mod)

    @set_ev_cls(topo_event.EventSwitchEnter)
    def _on_switch_enter(self, _ev):
        # Refresh runtime.json each switch-enter -- topology.py may have
        # written it between Ryu starting and the switches connecting.
        if not self.runtime:
            self._load_runtime()

        switches = get_switch(self, None)
        names: List[str] = []
        for sw in switches:
            dpid = sw.dp.id
            name = self.dpid_to_name.get(dpid)
            if name is None:
                # Fall back to dpid string so static mode still works
                # without runtime.json.
                name = f"dpid={dpid:x}"
            else:
                self.datapaths[name] = sw.dp
            names.append(name)

        links = get_link(self, None)
        self.logger.info(
            "Topology ready: switches=%s links=%s",
            names, [(lk.src.dpid, lk.dst.dpid) for lk in links],
        )

        if len(self.datapaths) >= 3 and not self.topology_ready:
            self.topology_ready = True
            self._install_path(PRIMARY_PATH)
            self.active_path = list(PRIMARY_PATH)
            self._publish_active_path(reason="startup")

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply(self, ev):
        body = ev.msg.body
        dpid = ev.msg.datapath.id
        switch_name = self.dpid_to_name.get(dpid)
        if switch_name is None:
            return
        for stat in body:
            self._absorb_port_stat(switch_name, stat)

    def _absorb_port_stat(self, switch_name: str, stat) -> None:
        port = int(stat.port_no)
        key = (switch_name, port)
        now = {
            "tx_bytes":   int(stat.tx_bytes),
            "tx_packets": int(stat.tx_packets),
            "tx_dropped": int(stat.tx_dropped),
            "rx_bytes":   int(stat.rx_bytes),
            "rx_packets": int(stat.rx_packets),
            "rx_dropped": int(stat.rx_dropped),
        }
        prev = self.last_counters.get(key)
        self.last_counters[key] = now
        if prev is None:
            return  # need 2 samples for a delta

        link_key, cap = self._port_to_link(switch_name, port)
        if link_key is None or cap <= 0.0:
            return

        d_bytes = max(0, now["tx_bytes"]   - prev["tx_bytes"])
        d_pkts  = max(0, now["tx_packets"] - prev["tx_packets"])
        d_drop  = max(0, now["tx_dropped"] - prev["tx_dropped"])

        throughput_mbps = (d_bytes * 8.0) / (STATS_INTERVAL * 1_000_000.0)
        util = min(throughput_mbps / cap if cap else 0.0, 2.0)
        base = BASE_LATENCY_MS.get(link_key, 1.0)
        # M/M/1-flavoured queueing-latency proxy (same shape as the
        # Track A simulator), bounded so 100% util doesn't divide by zero.
        latency_ms = base * (1.0 / max(1.0 - min(util, 0.99), 1e-6))
        attempted = d_pkts + d_drop
        loss_pct = (d_drop / attempted * 100.0) if attempted > 0 else 0.0

        row = {
            "throughput_mbps": float(throughput_mbps),
            "latency_ms":      float(latency_ms),
            "packet_loss_pct": float(loss_pct),
            "utilization":     float(util),
        }
        hist = self.history.setdefault(link_key, deque(maxlen=WINDOW_SIZE))
        hist.append(row)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofp = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return
        dpid = datapath.id

        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][eth.src] = in_port

        out_port = self.mac_to_port[dpid].get(eth.dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        out = parser.OFPPacketOut(
            datapath=datapath, buffer_id=msg.buffer_id, in_port=in_port,
            actions=actions, data=data,
        )
        datapath.send_msg(out)

    # ---- control loop ------------------------------------------------------

    def _monitor(self):
        while True:
            if self.topology_ready:
                try:
                    self._tick_once()
                except Exception as exc:  # pragma: no cover - keep loop alive
                    self.logger.exception("monitor tick failed: %s", exc)
            hub.sleep(STATS_INTERVAL)

    def _tick_once(self) -> None:
        # Step 1: sweep OFPPortStatsRequest across every switch. Replies
        # arrive asynchronously and feed self.history via
        # _port_stats_reply -> _absorb_port_stat.
        for dp in self.datapaths.values():
            parser = dp.ofproto_parser
            ofp = dp.ofproto
            dp.send_msg(parser.OFPPortStatsRequest(dp, 0, ofp.OFPP_ANY))

        self.tick += 1

        new_path = self._maybe_reroute()
        if new_path is not None and new_path != self.active_path:
            self.logger.info(
                "[tick %d] reroute %s -> %s (total reroutes=%d)",
                self.tick, self.active_path, new_path, self.reroutes_total + 1,
            )
            self.reroutes_total += 1
            self.active_path = list(new_path)
            self._install_path(new_path)
            self._publish_active_path(reason="decision_engine_reroute")
        else:
            # Even in steady state we re-stamp the JSON so the collector
            # knows our last decision is fresh (its staleness window is
            # 10 s; 1 Hz tick keeps us comfortably under that).
            self._publish_active_path(reason="steady_state")

    def _maybe_reroute(self) -> Optional[List[str]]:
        """Run the four-step predictive loop documented at the top of this
        file. Returns the path the controller wants to be on after this
        tick, or ``None`` when there isn't enough history yet (e.g. the
        first WINDOW_SIZE-1 ticks).
        """
        if MODE != "predictive":
            return None
        if self._forecaster is None or self._engine is None or self._Action is None:
            return None

        hist = self.history.get(PRIMARY_BOTTLENECK_LINK)
        if hist is None or len(hist) < WINDOW_SIZE:
            return None

        # Step 2: build a DataFrame in the schema LoadedForecaster expects
        # and ask for a horizon forecast on the primary bottleneck link.
        try:
            import pandas as pd  # imported lazily to avoid hard dep in static mode
        except ImportError:  # pragma: no cover
            self.logger.warning("pandas not available; cannot predict.")
            return None

        df = pd.DataFrame(list(hist))
        try:
            if FORECASTER == "persistence":
                import numpy as np
                forecast = np.full(
                    self._forecaster.horizon,
                    float(df["utilization"].iloc[-1]), dtype=np.float32,
                )
            else:
                forecast = self._forecaster.predict_link(df)
        except Exception as exc:  # pragma: no cover - model corner cases
            self.logger.warning("predict_link failed: %s", exc)
            return None
        if forecast is None:
            return None

        # Step 3: rule-based decision engine -> KEEP or REROUTE.
        decision = self._engine.step([forecast])
        self._log_decision(hist[-1]["utilization"], forecast, decision)
        if decision.action == self._Action.REROUTE:
            return list(ALTERNATE_PATH)
        return list(PRIMARY_PATH)

    def _log_decision(self, util_now, forecast, decision) -> None:
        """[informe] Una fila por tick en runtime/decisions_<MODE>_<FORECASTER>.csv
        y una linea de log cuando la prediccion pasa el umbral o cambia la accion."""
        import csv
        peak = float(decision.predicted_peak_utilization)
        action = decision.action.value
        path_csv = RUNTIME_DIR / f"decisions_{MODE}_{FORECASTER}.csv"
        new = not path_csv.exists()
        with path_csv.open("a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["ts_epoch", "tick", "util_s1s2", "forecast",
                            "pred_peak", "action", "reason"])
            w.writerow([f"{time.time():.3f}", self.tick, f"{util_now:.3f}",
                        " ".join(f"{v:.3f}" for v in forecast),
                        f"{peak:.3f}", action, decision.reason])
        prev = getattr(self, "_last_action", None)
        if peak >= 0.75 or action != prev:
            self.logger.info(
                "[tick %d] util s1-s2=%.2f | prediccion t+1..t+5=[%s] pico=%.2f -> %s (%s)",
                self.tick, util_now, " ".join(f"{v:.2f}" for v in forecast),
                peak, action, decision.reason,
            )
        self._last_action = action

    # ---- flow installation -------------------------------------------------

    def _install_path(self, path: List[str]) -> None:
        """Install priority-100 IPv4 routing flows for ``h1<->h2`` traffic
        along ``path`` on every switch in the path.

        Removes any previous priority-100 flows for the same dst IPs first
        so transitions are clean (we don't accumulate stale rules).
        """
        if not self.runtime:
            self.logger.warning("runtime.json not loaded yet; skipping flow install.")
            return

        hosts = self.runtime.get("hosts", {})
        h1_ip = hosts.get("h1", {}).get("ip", "10.0.0.1")
        h2_ip = hosts.get("h2", {}).get("ip", "10.0.0.2")

        for i, node in enumerate(path):
            if node not in self.datapaths:  # hosts have no datapath
                continue
            prev_node = path[i - 1]
            next_node = path[i + 1]
            dp = self.datapaths[node]
            parser = dp.ofproto_parser

            fwd_out = self._of_port(node, next_node)
            rev_out = self._of_port(node, prev_node)
            if fwd_out is None or rev_out is None:
                self.logger.warning(
                    "Cannot resolve OF ports for %s (next=%s prev=%s); "
                    "skipping flow install.", node, next_node, prev_node,
                )
                continue

            # Delete previous priority-100 IPv4 flows on this switch.
            self._del_flows(dp, 100, parser.OFPMatch(eth_type=0x0800, ipv4_dst=h2_ip))
            self._del_flows(dp, 100, parser.OFPMatch(eth_type=0x0800, ipv4_dst=h1_ip))
            # Step 4: install the new flows for both directions.
            self._add_flow(
                dp, 100,
                parser.OFPMatch(eth_type=0x0800, ipv4_dst=h2_ip),
                [parser.OFPActionOutput(int(fwd_out))],
            )
            self._add_flow(
                dp, 100,
                parser.OFPMatch(eth_type=0x0800, ipv4_dst=h1_ip),
                [parser.OFPActionOutput(int(rev_out))],
            )

        self.logger.info("Installed IPv4 flows for path %s", path)

    # ---- runtime.json lookup helpers --------------------------------------

    def _of_port(self, switch: str, facing: str) -> Optional[int]:
        port_map = self.runtime.get("port_map", {})
        entry = port_map.get(f"{switch}->{facing}", {})
        intf = entry.get("src_intf")
        if intf is None:
            return None
        port = self.runtime.get("switch_port_map", {}).get(switch, {}).get(intf)
        return int(port) if port is not None else None

    def _port_to_link(self, switch: str, of_port: int) -> Tuple[Optional[str], float]:
        ports = self.runtime.get("switch_port_map", {}).get(switch, {})
        intf_name: Optional[str] = None
        for name, p in ports.items():
            if int(p) == int(of_port):
                intf_name = name
                break
        if intf_name is None:
            return None, 0.0
        for key, entry in self.runtime.get("port_map", {}).items():
            if "->" not in key:
                continue
            src, dst = key.split("->", 1)
            if src == switch and entry.get("src_intf") == intf_name:
                for link in self.runtime.get("links", []):
                    if {link["src"], link["dst"]} == {src, dst}:
                        return f"{src}-{dst}", float(link["capacity_mbps"])
                return f"{src}-{dst}", 0.0
        return None, 0.0

    def _publish_active_path(self, reason: str) -> None:
        try:
            ACTIVE_PATH_JSON.write_text(json.dumps({
                "ts_epoch": time.time(),
                "tick": self.tick,
                "mode": MODE,
                "path": list(self.active_path),
                "reroutes_total": self.reroutes_total,
                "reason": reason,
            }))
        except OSError:
            pass
