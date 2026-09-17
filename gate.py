"""Front door. The only thing exposed. Identity is proven by the caller's own
GitLab PAT, so a booking cannot claim to be someone else."""
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from prefect.blocks.system import Secret

GITLAB = os.getenv("GITLAB_URL", "https://gitlab.com").rstrip("/")
PREFECT = os.getenv("PREFECT_API_URL", "http://prefect:4200/api")
AUTH = tuple(os.environ["PREFECT_API_AUTH_STRING"].split(":", 1)) if os.getenv("PREFECT_API_AUTH_STRING") else None
DEPLOYMENT = "run-pipeline/e2e"
PROTECTED = {r.strip() for r in os.getenv("PROTECTED_REFS", "main,beta,develop,ci-test,alpha-1.0.10").split(",")}
TAGS = [t.strip() for t in os.getenv("RUNNER_TAGS", "perentie-runner,tern-runner").split(",")]
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
ADVERTISE = os.getenv("ADVERTISE_HOST", "")
EXAMPLE_REF = os.getenv("DEFAULT_REF", "dev/1.0.13")  # shown in the dashboard example only
STORE = "/data/schedules.json"
_lock = threading.Lock()


def prefect_api(method, path, body=None):
    r = httpx.request(method, PREFECT + path, json=body, auth=AUTH, timeout=20)
    r.raise_for_status()
    return r.json() if r.content else None


def whoami(token):
    try:
        r = httpx.get(f"{GITLAB}/api/v4/user", headers={"PRIVATE-TOKEN": token}, timeout=15)
        return r.json()["username"] if r.status_code == 200 else None
    except Exception:
        return None


def stash(user, token):
    Secret(value=token).save(f"gitlab-token-{user}", overwrite=True)


def check_ref(b):
    ref = b.get("ref")
    if not ref:
        raise ValueError("ref is required: name the branch this runs against")
    if ref in PROTECTED and not b.get("allow_protected"):
        raise ValueError(f"{ref} is a publish branch; pass allow_protected if you mean it")
    # An unknown tag has no concurrency limit behind it, so it would skip the
    # queue and run immediately no matter what else is in flight.
    tag = b.get("runner_tag", TAGS[0])
    if tag not in TAGS:
        raise ValueError(f"unknown runner_tag {tag!r}; expected one of {', '.join(TAGS)}")
    b["runner_tag"] = tag


def book(user, b):
    check_ref(b)
    dep = prefect_api("GET", f"/deployments/name/{DEPLOYMENT}")
    params = {k: b[k] for k in ("ref", "runner_tag", "variables", "note", "allow_protected") if k in b}
    params["requested_by"] = user
    at = b.get("scheduled_time") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    run = prefect_api("POST", f"/deployments/{dep['id']}/create_flow_run", {
        "parameters": params,
        "state": {"type": "SCHEDULED", "state_details": {"scheduled_time": at}},
    })
    return {
        "id": run["id"],
        "at": run["state"]["state_details"]["scheduled_time"],
        "ref": params["ref"],
        "runner_tag": params["runner_tag"],
        "requested_by": user,
        "variables": params.get("variables", {}),
        "note": params.get("note", ""),
        "run_name": run["name"],
    }


def bookings():
    """Time, who, and an id to act on. Nothing about what the run does."""
    runs = prefect_api("POST", "/flow_runs/filter", {
        "flow_runs": {"state": {"type": {"any_": ["SCHEDULED", "PENDING", "RUNNING"]}}},
        "sort": "EXPECTED_START_TIME_ASC",
        "limit": 50,
    })
    return [{
        "id": r["id"],
        "at": r["state"]["state_details"]["scheduled_time"],
        "requested_by": r["parameters"].get("requested_by"),
    } for r in runs]


def recent():
    """Finished runs, so a booking that has already fired is still visible."""
    runs = prefect_api("POST", "/flow_runs/filter", {
        "flow_runs": {"state": {"type": {"any_": ["COMPLETED", "FAILED", "CRASHED", "CANCELLED"]}}},
        "sort": "END_TIME_DESC",
        "limit": 10,
    })
    return [{
        "id": r["id"],
        "at": r.get("end_time"),
        "state": r["state"]["type"],
        "requested_by": r["parameters"].get("requested_by"),
    } for r in runs]


def fields():
    """What you may put in a /book payload. Driven by config so it cannot drift."""
    return [
        {"name": "ref", "default": "required",
         "what": "Branch to run against."},
        {"name": "variables", "default": "{}",
         "what": "GitLab pipeline variables. Values must be strings, so \"true\" not true."},
        {"name": "scheduled_time", "default": "now",
         "what": "ISO 8601 UTC, e.g. 2026-09-18T02:00:00Z. Omit to run as soon as a slot frees."},
        {"name": "runner_tag", "default": TAGS[0],
         "what": "One of " + ", ".join(TAGS) + ". Each has its own queue of one."},
        {"name": "note", "default": "none",
         "what": "Free text label. Reaches the pipeline as part of SCHEDULER_LABEL."},
        {"name": "allow_protected", "default": "false",
         "what": "Required to book " + ", ".join(sorted(PROTECTED)) + "."},
    ]


def deployment_id():
    return prefect_api("GET", f"/deployments/name/{DEPLOYMENT}")["id"]


def load_store():
    try:
        with open(STORE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_store(d):
    with open(STORE + ".tmp", "w") as f:
        json.dump(d, f, indent=1)
    os.replace(STORE + ".tmp", STORE)


def reconcile():
    """serve() re-registers the deployment on every start and drops its
    schedules, so this store is the source of truth and re-applies them."""
    with _lock:
        store = load_store()
        if not store:
            return
        dep = deployment_id()
        live = {s["id"] for s in prefect_api("GET", f"/deployments/{dep}/schedules")}
        missing = {k: v for k, v in store.items() if k not in live}
        if not missing:
            return
        for old_id, spec in missing.items():
            made = prefect_api("POST", f"/deployments/{dep}/schedules", [spec])
            store.pop(old_id)
            store[made[0]["id"]] = spec
        save_store(store)


def schedules():
    reconcile()
    return [{
        "id": s["id"],
        "cron": s["schedule"].get("cron"),
        "timezone": s["schedule"].get("timezone"),
        "requested_by": (s.get("parameters") or {}).get("requested_by"),
    } for s in prefect_api("GET", f"/deployments/{deployment_id()}/schedules")]


def add_schedule(user, b):
    check_ref(b)
    params = {k: b[k] for k in ("ref", "runner_tag", "variables", "note", "allow_protected") if k in b}
    params["requested_by"] = user
    spec = {
        "schedule": {"cron": b["cron"], "timezone": b.get("timezone", "UTC")},
        "active": True,
        "parameters": params,
    }
    made = prefect_api("POST", f"/deployments/{deployment_id()}/schedules", [spec])
    with _lock:
        store = load_store()
        store[made[0]["id"]] = spec
        save_store(store)
    return {
        "id": made[0]["id"],
        "cron": b["cron"],
        "timezone": b.get("timezone", "UTC"),
        "ref": params["ref"],
        "runner_tag": params["runner_tag"],
        "requested_by": user,
        "variables": params.get("variables", {}),
        "note": params.get("note", ""),
    }


def drop_schedule(b):
    check_id(b["schedule_id"])
    prefect_api("DELETE", f"/deployments/{deployment_id()}/schedules/{b['schedule_id']}")
    with _lock:
        store = load_store()
        store.pop(b["schedule_id"], None)
        save_store(store)
    return {"removed": b["schedule_id"]}


def check_id(value):
    if not re.fullmatch(UUID, value or ""):
        raise PermissionError("bad id")
    return value


def move(b):
    check_id(b["run_id"])
    r = prefect_api("POST", f"/flow_runs/{b['run_id']}/set_state", {
        "state": {"type": "SCHEDULED", "state_details": {"scheduled_time": b["scheduled_time"]}},
        "force": True,
    })
    return {"status": r["status"], "at": r["state"]["state_details"]["scheduled_time"]}


def cancel(b):
    check_id(b["run_id"])
    prefect_api("DELETE", f"/flow_runs/{b['run_id']}")
    return {"cancelled": b["run_id"]}


# A PAT is only needed where one will eventually trigger CI. Slots are a shared
# team resource, so anyone can reschedule or release one.
AUTHED = {"/book": book, "/schedules/add": add_schedule}
OPEN = {"/move": move, "/cancel": cancel, "/schedules/remove": drop_schedule}
READS = {"/bookings": bookings, "/schedules": schedules, "/recent": recent, "/fields": fields}

# The client, served from here so a private repo is not in the way.
CLIENT = {
    "/client/mcp_server.py": ("mcp_server.py", "text/x-python"),
    "/client/book.py": ("book.py", "text/x-python"),
    "/client/SKILL.md": ("skill/SKILL.md", "text/markdown"),
}


def install_help(host):
    host = ADVERTISE or host
    return f"""# pipeline-scheduler, one line each. Use your own GitLab PAT.

# MCP (agents). Claude runs this file on every session, so it lives under
# ~/.claude beside the skill, never in whatever repo you happen to be standing
# in. --scope user registers it for every project, terminal and VS Code alike.
mkdir -p ~/.claude/mcp/pipeline-scheduler && curl -sf http://{host}/client/mcp_server.py -o ~/.claude/mcp/pipeline-scheduler/mcp_server.py && claude mcp add pipeline-scheduler --scope user -e GITLAB_TOKEN=glpat-xxx -e SCHEDULER_URL=http://{host} -- python3 ~/.claude/mcp/pipeline-scheduler/mcp_server.py

# Skill (optional, tells agents when to book)
mkdir -p ~/.claude/skills/pipeline-book && curl -sf http://{host}/client/SKILL.md -o ~/.claude/skills/pipeline-book/SKILL.md

# CLI only
mkdir -p ~/.local/bin && curl -sf http://{host}/client/book.py -o ~/.local/bin/book.py && chmod +x ~/.local/bin/book.py && export SCHEDULER_URL=http://{host}

# Upgrade later: re-run the curl for whichever you installed. No need to re-add.
"""


def quick_book(host):
    host = ADVERTISE or host
    return (
        f"""curl -sf -X POST http://{host}/book -H "Authorization: Bearer $GITLAB_TOKEN" """
        f"""-H 'Content-Type: application/json' """
        f"""-d '{{"ref":"{EXAMPLE_REF}","variables":{{"RUN_BUILD":"true","RUN_UNIT_TESTS":"false"}}}}'"""
    )


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, code, obj):
        body = (json.dumps(obj, indent=2) + "\n").encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def auth(self):
        header = self.headers.get("Authorization", "")
        token = header[7:].strip() if header.startswith("Bearer ") else ""
        if not token.isascii() or len(token) > 512:
            token = ""
        user = whoami(token) if token else None
        if not user:
            self.reply(401, {"error": "send your GitLab PAT as Authorization: Bearer <token>"})
            return None, None
        return user, token

    def send_text(self, body, ctype):
        raw = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            return self.send_text(open("dashboard.html").read(), "text/html; charset=utf-8")
        if self.path == "/install":
            return self.send_text(install_help(self.headers.get("Host", "localhost:8080")), "text/plain")
        if self.path == "/quickbook":
            return self.send_text(quick_book(self.headers.get("Host", "localhost:8080")), "text/plain")
        if self.path in CLIENT:
            name, ctype = CLIENT[self.path]
            return self.send_text(open(name).read(), ctype)
        handler = READS.get(self.path)
        if not handler:
            return self.reply(404, {"error": "no such endpoint"})
        try:
            self.reply(200, {self.path.strip("/"): handler()})
        except Exception as e:
            self.reply(400, {"error": f"{type(e).__name__}: {e}"})

    def do_POST(self):
        authed, handler = self.path in AUTHED, AUTHED.get(self.path) or OPEN.get(self.path)
        if not handler:
            return self.reply(404, {"error": "no such endpoint"})
        user = token = None
        if authed:
            user, token = self.auth()
            if not user:
                return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}")
            if authed:
                stash(user, token)
                self.reply(200, handler(user, body))
            else:
                self.reply(200, handler(body))
        except PermissionError as e:
            self.reply(403, {"error": str(e)})
        except Exception as e:
            self.reply(400, {"error": f"{type(e).__name__}: {e}"})

    def log_message(self, fmt, *a):
        pass


def reconciler():
    while True:
        try:
            reconcile()
        except Exception:
            pass
        time.sleep(20)


if __name__ == "__main__":
    threading.Thread(target=reconciler, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
