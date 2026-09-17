#!/bin/sh
set -e

prefect server start --host 0.0.0.0 --port 4200 &

until python -c "import httpx,sys;sys.exit(0 if httpx.get('$PREFECT_API_URL/health').status_code==200 else 1)" 2>/dev/null; do
  sleep 2
done

for tag in $(echo "${RUNNER_TAGS:-perentie-runner,tern-runner}" | tr ',' ' '); do
  prefect global-concurrency-limit create --limit 1 "$tag" || true
done

exec python /app/scheduler.py
