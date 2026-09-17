#!/bin/sh
# Starts the stack and advertises this machine's LAN address in the install
# strings, so teammates do not copy a localhost URL.
set -e
cd "$(dirname "$0")"
[ -f .env ] || { echo "no .env: cp .env.example .env and fill it in"; exit 1; }

IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
[ -n "$IP" ] || IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "$IP" ] || { echo "could not work out this machine's IP"; exit 1; }

PORT=$(grep -E '^GATE_PORT=' .env | cut -d= -f2)
PORT=${PORT:-8080}

ADVERTISE_HOST="$IP:$PORT" docker compose up -d --build "$@"
echo
echo "dashboard  http://$IP:$PORT"
echo "install    curl http://$IP:$PORT/install"
