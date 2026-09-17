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
# Nothing can legitimately be mid-run at startup: this container is the only
# thing that executes flows. So any run still marked RUNNING was killed by a
# restart, and its concurrency slot is leaked. Clear both, or the queue wedges
# forever with no sign of why.
python - <<'PY'
import os, httpx
auth = tuple(os.environ["PREFECT_API_AUTH_STRING"].split(":", 1)) if os.getenv("PREFECT_API_AUTH_STRING") else None
api = "http://127.0.0.1:4200/api"
# PENDING never reached the flow body, so it cannot have triggered anything:
# put it back in the queue rather than losing the booking. RUNNING may have
# already POSTed to GitLab, so re-running it could trigger a second pipeline.
# Those are crashed and alerted on instead, for a human to judge.
from datetime import datetime, timezone
now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

pending = httpx.post(f"{api}/flow_runs/filter", auth=auth, timeout=20,
                     json={"flow_runs": {"state": {"type": {"any_": ["PENDING"]}}}, "limit": 200}).json()
for r in pending:
    httpx.post(f"{api}/flow_runs/{r['id']}/set_state", auth=auth, timeout=20, json={
        "state": {"type": "SCHEDULED", "state_details": {"scheduled_time": now},
                  "message": "requeued after a scheduler restart"}, "force": True})
if pending:
    print(f"requeued {len(pending)} booking(s) that had not started")

running = httpx.post(f"{api}/flow_runs/filter", auth=auth, timeout=20,
                     json={"flow_runs": {"state": {"type": {"any_": ["RUNNING"]}}}, "limit": 200}).json()
for r in running:
    httpx.post(f"{api}/flow_runs/{r['id']}/set_state", auth=auth, timeout=20, json={
        "state": {"type": "CRASHED", "message": "orphaned by a scheduler restart; "
                  "check GitLab in case its pipeline is still running"}, "force": True})
if running:
    print(f"crashed {len(running)} run(s) orphaned by a restart")

for lim in httpx.post(f"{api}/v2/concurrency_limits/filter", auth=auth, timeout=20, json={}).json():
    if lim["active_slots"]:
        httpx.patch(f"{api}/v2/concurrency_limits/{lim['name']}", auth=auth, timeout=20,
                    json={"active_slots": 0})
        print(f"released {lim['active_slots']} leaked slot(s) on {lim['name']}")
PY

echo "concurrency limits ready: ${RUNNER_TAGS:-perentie-runner,tern-runner}"
echo "prefect api (internal only): http://prefect:4200/api"
echo "admin ui (only with --ui):   http://127.0.0.1:${PREFECT_UI_PORT:-4200}"

exec python /app/scheduler.py
