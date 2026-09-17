#!/bin/sh
set -e

# Prefect's own banner prints its bind address (0.0.0.0), which is not a URL
# anyone uses. Drop it and print the real ones below.
prefect server start --host 0.0.0.0 --port 4200 2>&1 | grep -vE "0\.0\.0\.0:4200|^ *\||^$" &

until python -c "import httpx,sys;sys.exit(0 if httpx.get('$PREFECT_API_URL/health').status_code==200 else 1)" 2>/dev/null; do
  sleep 2
done

for tag in $(echo "${RUNNER_TAGS:-perentie-runner,tern-runner}" | tr ',' ' '); do
  prefect global-concurrency-limit inspect "$tag" >/dev/null 2>&1 ||
    prefect global-concurrency-limit create --limit 1 "$tag" >/dev/null
done
echo "concurrency limits ready: ${RUNNER_TAGS:-perentie-runner,tern-runner}"
echo "prefect api (internal only): http://prefect:4200/api"
echo "admin ui (only with --ui):   http://127.0.0.1:${PREFECT_UI_PORT:-4200}"

exec python /app/scheduler.py
