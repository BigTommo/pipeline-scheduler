"""Front door. The only thing exposed. Identity is proven by the caller's own
GitLab PAT, so a booking cannot claim to be someone else."""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from prefect.blocks.system import Secret

GITLAB = os.getenv("GITLAB_URL", "https://gitlab.com").rstrip("/")
PREFECT = os.getenv("PREFECT_API_URL", "http://prefect:4200/api")
AUTH = tuple(os.environ["PREFECT_API_AUTH_STRING"].split(":", 1)) if os.getenv("PREFECT_API_AUTH_STRING") else None
DEPLOYMENT = "run-pipeline/e2e"
DEFAULT_REF = os.getenv("DEFAULT_REF", "dev/1.0.13")
PROTECTED = {r.strip() for r in os.getenv("PROTECTED_REFS", "main,beta,develop,ci-test,alpha-1.0.10").split(",")}
UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


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


def book(user, b):
    if b.get("ref", DEFAULT_REF) in PROTECTED and not b.get("allow_protected"):
        raise ValueError(f"{b.get('ref', DEFAULT_REF)} is a publish branch; pass allow_protected if you mean it")
    dep = prefect_api("GET", f"/deployments/name/{DEPLOYMENT}")
    params = {k: b[k] for k in ("ref", "runner_tag", "variables", "note", "allow_protected") if k in b}
    params["requested_by"] = user
    run = prefect_api("POST", f"/deployments/{dep['id']}/create_flow_run", {
        "parameters": params,
        "state": {"type": "SCHEDULED", "state_details": {"scheduled_time": b["scheduled_time"]}},
    })
    return {"id": run["id"], "name": run["name"], "at": run["state"]["state_details"]["scheduled_time"]}


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


def deployment_id():
    return prefect_api("GET", f"/deployments/name/{DEPLOYMENT}")["id"]


def schedules():
    return [{
        "id": s["id"],
        "cron": s["schedule"].get("cron"),
        "timezone": s["schedule"].get("timezone"),
        "requested_by": (s.get("parameters") or {}).get("requested_by"),
    } for s in prefect_api("GET", f"/deployments/{deployment_id()}/schedules")]


def add_schedule(user, b):
    if b.get("ref", DEFAULT_REF) in PROTECTED and not b.get("allow_protected"):
        raise ValueError(f"{b.get('ref', DEFAULT_REF)} is a publish branch; pass allow_protected if you mean it")
    params = {k: b[k] for k in ("ref", "runner_tag", "variables", "note", "allow_protected") if k in b}
    params["requested_by"] = user
    made = prefect_api("POST", f"/deployments/{deployment_id()}/schedules", [{
        "schedule": {"cron": b["cron"], "timezone": b.get("timezone", "UTC")},
        "active": True,
        "parameters": params,
    }])
    return {"id": made[0]["id"], "cron": b["cron"], "requested_by": user}


def drop_schedule(b):
    prefect_api("DELETE", f"/deployments/{deployment_id()}/schedules/{b['schedule_id']}")
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
READS = {"/bookings": bookings, "/schedules": schedules}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def reply(self, code, obj):
        body = json.dumps(obj).encode()
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

    def do_GET(self):
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


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
