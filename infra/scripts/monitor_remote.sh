#!/usr/bin/env bash
# Live-stream bot logs from the remote server.
# Usage:
#   ./infra/scripts/monitor_remote.sh          # both bots
#   ./infra/scripts/monitor_remote.sh axs      # only AXS
#   ./infra/scripts/monitor_remote.sh sand     # only SAND

set -euo pipefail

HOST="root@134.209.3.234"
KEY="$HOME/.ssh/binance_trader_deploy"
SERVICE="${1:-}"

if [ -n "$SERVICE" ]; then
    exec ssh -i "$KEY" "$HOST" "cd /opt/binance-trader && docker compose logs -f --tail=50 bot-${SERVICE}"
else
    exec ssh -i "$KEY" "$HOST" "cd /opt/binance-trader && docker compose logs -f --tail=50"
fi
