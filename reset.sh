#!/bin/sh
# Wipes Prefect's database: every booking, run and stored token.
#
#   ./reset.sh          keep recurring schedules (restored from schedules.json)
#   ./reset.sh --all    drop recurring schedules too
set -e
cd "$(dirname "$0")"

KEEP=1
case "$1" in
  "") ;;
  --all) KEEP="" ;;
  *) echo "unknown option: $1"; exit 1 ;;
esac

printf 'Deletes every booking, run and stored token'
if [ -n "$KEEP" ]; then printf '. Recurring schedules are kept.\n'
else printf ', and recurring schedules.\n'; fi
printf 'Type yes to continue: '
read -r ANSWER
[ "$ANSWER" = "yes" ] || { echo "aborted"; exit 1; }

[ -n "$KEEP" ] || docker compose exec -T gate sh -c 'echo "{}" > /data/schedules.json'

docker compose exec -T prefect prefect server database reset -y
docker compose restart prefect

printf 'waiting for the deployment to re-register'
until docker compose logs --tail=40 prefect 2>&1 | grep -q "run-pipeline/e2e"; do
  printf '.'; sleep 2
done
echo

if [ -n "$KEEP" ]; then
  sleep 5
  docker compose exec -T gate python -c "
import json, urllib.request
n = len(json.load(urllib.request.urlopen('http://127.0.0.1:8080/schedules'))['schedules'])
print(f'{n} recurring schedule(s) restored')"
fi

echo "done. Each person's token is stored again on their next booking."
