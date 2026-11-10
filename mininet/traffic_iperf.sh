#!/usr/bin/env bash
# Track B traffic driver -- reproduces Track A's offered-load shape on a
# real Mininet/OVS data plane using iperf3.
#
# Each subcommand is meant to be invoked inside the appropriate Mininet
# host namespace, either from the Mininet CLI:
#
#     mininet> h2 bash mininet/traffic_iperf.sh server &
#     mininet> h1 bash mininet/traffic_iperf.sh ping   &
#     mininet> h1 bash mininet/traffic_iperf.sh client congestion
#
# or via mnexec from another shell once mininet/runtime.json exists:
#
#     mnexec -a $(jq -r .hosts.h1.pid mininet/runtime.json) \
#         bash mininet/traffic_iperf.sh client congestion
#
# UDP is used (not TCP) so that packet loss shows up in OVS port counters
# (tx_dropped) and the Track A evaluator's loss column has real signal:
# TCP would back off and hide drops behind retransmits.
#
# Subcommands:
#   server                  - iperf3 -s on $BASE_PORT and $BURST_PORT (run on h2)
#   ping                    - continuous 1Hz ping h1->h2, logs RTT to runtime/ping.log
#   client normal           - 25 Mbps UDP for $DURATION seconds
#   client congestion       - 25 Mbps baseline + three ~95 Mbps overlays at
#                             ticks (120,200) (320,410) (500,560)
#                             so total during a burst ~= 120 Mbps (matches
#                             Track A's TrafficConfig.burst_mean_mbps)

set -euo pipefail

MODE="${1:-help}"
H2_IP="${H2_IP:-10.0.0.2}"
BASE_PORT="${BASE_PORT:-5201}"
BURST_PORT="${BURST_PORT:-5202}"
DURATION="${DURATION:-600}"
BASE_RATE_MBPS="${BASE_RATE_MBPS:-25}"
BURST_OVERLAY_MBPS="${BURST_OVERLAY_MBPS:-95}"

# Burst windows mirror src/config.py TrafficConfig.burst_windows:
#   (120, 200), (320, 410), (500, 560)
BURST_STARTS=(120 320 500)
BURST_ENDS=(200 410 560)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGDIR="${LOGDIR:-$SCRIPT_DIR/runtime}"
mkdir -p "$LOGDIR"

_log() { printf '[traffic:%s] %s\n' "$MODE" "$*"; }

case "$MODE" in
    server)
        _log "iperf3 -s on :$BASE_PORT and :$BURST_PORT"
        rm -f "$LOGDIR"/iperf_server_*.json
        iperf3 -s -p "$BASE_PORT"  -J --logfile "$LOGDIR/iperf_server_base.json"  &
        SRV_BASE=$!
        iperf3 -s -p "$BURST_PORT" -J --logfile "$LOGDIR/iperf_server_burst.json" &
        SRV_BURST=$!
        trap 'kill '"$SRV_BASE"' '"$SRV_BURST"' 2>/dev/null || true' EXIT INT TERM
        wait
        ;;

    ping)
        _log "perpetual 1Hz ping -> $LOGDIR/ping.log (RTT for latency_ms)"
        : > "$LOGDIR/ping.log"
        # -D prefixes each line with [unix_epoch.usec]; the collector tails
        # this file and pulls per-tick median RTT for effective_latency_ms.
        exec ping -i 1 -W 2 -D "$H2_IP" >> "$LOGDIR/ping.log" 2>&1
        ;;

    client)
        SCENARIO="${2:-normal}"
        : > "$LOGDIR/client.log"
        case "$SCENARIO" in
            normal)
                _log "normal: ${BASE_RATE_MBPS} Mbps UDP for ${DURATION}s" | tee -a "$LOGDIR/client.log"
                iperf3 -c "$H2_IP" -p "$BASE_PORT" -u \
                    -b "${BASE_RATE_MBPS}M" -t "$DURATION" -i 5 \
                    -J --logfile "$LOGDIR/iperf_client_base.json"
                ;;
            congestion)
                _log "congestion: ${BASE_RATE_MBPS} Mbps baseline + 3 x ${BURST_OVERLAY_MBPS} Mbps overlays" | tee -a "$LOGDIR/client.log"
                # Baseline runs the whole window in the background.
                iperf3 -c "$H2_IP" -p "$BASE_PORT" -u \
                    -b "${BASE_RATE_MBPS}M" -t "$DURATION" -i 5 \
                    -J --logfile "$LOGDIR/iperf_client_base.json" &
                BASE_PID=$!
                T0=$(date +%s)
                # Schedule each burst to start at its absolute offset from T0
                # (robust against the previous burst overrunning slightly).
                for i in 0 1 2; do
                    START="${BURST_STARTS[$i]}"
                    END="${BURST_ENDS[$i]}"
                    DUR=$((END - START))
                    NOW=$(( $(date +%s) - T0 ))
                    if (( NOW < START )); then sleep $((START - NOW)); fi
                    _log "  burst $((i+1)): t=${START}s..${END}s (${DUR}s @ ${BURST_OVERLAY_MBPS} Mbps overlay)" \
                        | tee -a "$LOGDIR/client.log"
                    iperf3 -c "$H2_IP" -p "$BURST_PORT" -u \
                        -b "${BURST_OVERLAY_MBPS}M" -t "$DUR" -i 5 \
                        -J --logfile "$LOGDIR/iperf_client_burst_${START}.json" \
                        || _log "  burst $((i+1)) iperf3 exited non-zero (continuing)"
                done
                wait "$BASE_PID" || true
                ;;
            *)
                echo "Unknown scenario: $SCENARIO (use 'normal' or 'congestion')" >&2
                exit 1
                ;;
        esac
        ;;

    help|*)
        cat <<EOF
Usage:
  $0 server                  # iperf3 servers on :$BASE_PORT and :$BURST_PORT (run on h2)
  $0 ping                    # continuous 1Hz ping h1->h2, logs to runtime/ping.log
  $0 client normal           # 25 Mbps UDP for ${DURATION}s
  $0 client congestion       # 25 Mbps + 3 x 95 Mbps bursts at (120,200) (320,410) (500,560)

Typical run from Mininet CLI (each line in its own shell so they pipeline):
  mininet> xterm h2 ; in xterm: bash mininet/traffic_iperf.sh server
  mininet> xterm h1 ; in xterm: bash mininet/traffic_iperf.sh ping
  mininet> xterm h1 ; in xterm: bash mininet/traffic_iperf.sh client congestion

Logs land under: $LOGDIR/
  ping.log                   - 1Hz timestamped RTTs, parsed by collect_metrics.py
  iperf_server_*.json        - server-side per-flow summaries (loss/jitter)
  iperf_client_*.json        - client-side per-flow summaries
  client.log                 - human-readable scenario timeline
EOF
        ;;
esac
