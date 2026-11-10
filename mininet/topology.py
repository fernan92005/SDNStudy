"""Mininet topology mirroring `src/config.TopologyConfig`.

Run as:

    sudo python3 mininet/topology.py

Requires Mininet + Open vSwitch installed on the host.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def build() -> None:
    try:
        from mininet.cli import CLI
        from mininet.link import TCLink
        from mininet.net import Mininet
        from mininet.node import OVSSwitch, RemoteController
    except ImportError as exc:  # pragma: no cover - stretch goal
        sys.stderr.write(
            "Mininet is not installed. See mininet/README.md for WSL2 setup.\n"
        )
        raise SystemExit(1) from exc

    net = Mininet(controller=RemoteController, switch=OVSSwitch, link=TCLink)

    c0 = net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6633)

    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h2 = net.addHost("h2", ip="10.0.0.2/24")

    s1 = net.addSwitch("s1")
    s2 = net.addSwitch("s2")
    s3 = net.addSwitch("s3")

    l_h1s1 = net.addLink(h1, s1, bw=1000, delay="0.1ms")
    l_s2h2 = net.addLink(s2, h2, bw=1000, delay="0.1ms")

    # Capacities and base latencies mirror Track A's src/config.py
    # (LinkSpec entries in TopologyConfig.links). Track B used to set the
    # s1<->s3 and s3<->s2 links to 80 Mbps, which was inconsistent with the
    # 180 Mbps used by the Track A fluid simulator -- ECMP and predictive
    # numbers would not be directly comparable. They are now aligned.
    l_s1s2 = net.addLink(s1, s2, bw=100, delay="2ms")
    l_s1s3 = net.addLink(s1, s3, bw=180, delay="3ms")
    l_s3s2 = net.addLink(s3, s2, bw=180, delay="3ms")

    net.build()
    c0.start()
    for s in (s1, s2, s3):
        s.start([c0])
    net.start()

    # Build a port map keyed by (src_node, dst_node) -> interface name on
    # src_node. collect_metrics.py uses this to know which OVS port number
    # to read for each link, which avoids brittle "s1-eth2 must be the one
    # facing s2" assumptions that would break if anyone reorders addLink().
    def _intf_name(link, node):
        return link.intf1.name if link.intf1.node is node else link.intf2.name

    port_map = {
        ("h1", "s1"): {"src_intf": _intf_name(l_h1s1, h1), "dst_intf": _intf_name(l_h1s1, s1)},
        ("s1", "h1"): {"src_intf": _intf_name(l_h1s1, s1), "dst_intf": _intf_name(l_h1s1, h1)},
        ("s1", "s2"): {"src_intf": _intf_name(l_s1s2, s1), "dst_intf": _intf_name(l_s1s2, s2)},
        ("s2", "s1"): {"src_intf": _intf_name(l_s1s2, s2), "dst_intf": _intf_name(l_s1s2, s1)},
        ("s1", "s3"): {"src_intf": _intf_name(l_s1s3, s1), "dst_intf": _intf_name(l_s1s3, s3)},
        ("s3", "s1"): {"src_intf": _intf_name(l_s1s3, s3), "dst_intf": _intf_name(l_s1s3, s1)},
        ("s3", "s2"): {"src_intf": _intf_name(l_s3s2, s3), "dst_intf": _intf_name(l_s3s2, s2)},
        ("s2", "s3"): {"src_intf": _intf_name(l_s3s2, s2), "dst_intf": _intf_name(l_s3s2, s3)},
        ("s2", "h2"): {"src_intf": _intf_name(l_s2h2, s2), "dst_intf": _intf_name(l_s2h2, h2)},
        ("h2", "s2"): {"src_intf": _intf_name(l_s2h2, h2), "dst_intf": _intf_name(l_s2h2, s2)},
    }

    # Resolve OF port numbers for each switch-side interface. Mininet sets
    # node.ports[intf] but only for the local side; we also stash the
    # datapath ids so the Ryu app can sanity-check dpids against names.
    dpid_map = {s.name: int(s.dpid, 16) for s in (s1, s2, s3)}
    switch_port_map = {}
    for s in (s1, s2, s3):
        switch_port_map[s.name] = {intf.name: s.ports[intf] for intf in s.intfList() if intf.name != "lo"}

    runtime_path = Path(__file__).resolve().parent / "runtime.json"
    runtime_path.write_text(
        json.dumps(
            {
                "hosts": {h.name: {"pid": h.pid, "ip": h.IP()} for h in (h1, h2)},
                "switches": [s.name for s in (s1, s2, s3)],
                "dpids": dpid_map,
                "switch_port_map": switch_port_map,
                "port_map": {f"{k[0]}->{k[1]}": v for k, v in port_map.items()},
                "primary_path": ["h1", "s1", "s2", "h2"],
                "alternate_path": ["h1", "s1", "s3", "s2", "h2"],
                "links": [
                    {"src": "h1", "dst": "s1", "capacity_mbps": 1000.0},
                    {"src": "s1", "dst": "s2", "capacity_mbps": 100.0},
                    {"src": "s1", "dst": "s3", "capacity_mbps": 180.0},
                    {"src": "s3", "dst": "s2", "capacity_mbps": 180.0},
                    {"src": "s2", "dst": "h2", "capacity_mbps": 1000.0},
                ],
            },
            indent=2,
        )
    )
    print(f"Wrote {runtime_path}")

    print("Topology up. Try: pingall")
    CLI(net)
    net.stop()
    try:
        runtime_path.unlink()
    except FileNotFoundError:
        pass


if __name__ == "__main__":
    build()
