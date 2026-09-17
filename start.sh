#!/bin/sh
# Starts the stack and advertises this machine's LAN address in the install
# strings, so teammates do not copy a localhost URL.
#
#   ./start.sh            start detached
#   ./start.sh -f         start, then follow the logs (Ctrl-C leaves it running)
#   ./start.sh --ui       also expose Prefect's own UI on 127.0.0.1
set -e
cd "$(dirname "$0")"
[ -f .env ] || { echo "no .env: cp .env.example .env and fill it in"; exit 1; }

FOLLOW=""
FILES="-f docker-compose.yml"
for arg in "$@"; do
  case "$arg" in
    -f|--follow|--logs) FOLLOW=1 ;;
    --ui) FILES="$FILES -f docker-compose.ui.yml" ;;
    *) echo "unknown option: $arg"; exit 1 ;;
  esac
done

IP=$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1)}' | head -1)
[ -n "$IP" ] || IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -n "$IP" ] || { echo "could not work out this machine's IP"; exit 1; }

PORT=$(grep -E '^GATE_PORT=' .env | cut -d= -f2)
PORT=${PORT:-8080}

# shellcheck disable=SC2086
ADVERTISE_HOST="$IP:$PORT" docker compose $FILES up -d --build

echo
echo "dashboard  http://$IP:$PORT"
echo "install    curl http://$IP:$PORT/install"
case "$FILES" in *ui.yml*) echo "admin      http://127.0.0.1:${PREFECT_UI_PORT:-4200}" ;; esac

if [ -n "$FOLLOW" ]; then
  echo
  echo "following logs, Ctrl-C detaches and leaves the stack running"
  echo
  # shellcheck disable=SC2086
  exec docker compose $FILES logs -f --tail=20
fi
