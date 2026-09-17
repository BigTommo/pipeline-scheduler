#!/usr/bin/env python3
"""Stdio MCP server for pipeline-scheduler. Single file, stdlib only.

Your PAT comes from this process's own GITLAB_TOKEN env, never from a tool
argument, so it is never in the model's context."""
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

GATE = os.getenv("SCHEDULER_URL", "http://localhost:8080").rstrip("/")
TOKEN = os.getenv("GITLAB_TOKEN", "")


def call(method, path, body=None):
    req = urllib.request.Request(
        GATE + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or "null")
    except urllib.error.HTTPError as e:
        raise RuntimeError(json.loads(e.read() or '{"error":"?"}').get("error", e.reason)) from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"scheduler unreachable at {GATE}: {e.reason}") from None


def when(at, delay):
    if delay:
        n, unit = int(re.match(r"(\d+)", delay).group(1)), delay[-1]
        t = datetime.now(timezone.utc) + timedelta(minutes=n * {"m": 1, "h": 60, "d": 1440}[unit])
    elif at:
        t = datetime.fromisoformat(at).astimezone(timezone.utc)
    else:
        t = datetime.now(timezone.utc)
    return t.isoformat().replace("+00:00", "Z")

TIME = {
    "start_in": {"type": "string", "description": "Relative delay: 30m, 2h, 1d. Omit both for immediately."},
    "start_at": {"type": "string", "description": "Local time, e.g. 2026-09-18 02:00."},
}

TOOLS = [
    {
        "name": "list_bookings",
        "description": "List pipeline runs booked, waiting for a runner slot, or running.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "book_pipeline",
        "description": "Book a GitLab E2E pipeline run. It waits for a free slot on its runner tag instead of contending. Runs as you; there is no way to book on someone else's behalf.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "runner_tag": {"type": "string", "enum": ["perentie-runner", "tern-runner"]},
                "variables": {"type": "object", "description": "GitLab pipeline variables, string values."},
                "ref": {"type": "string", "description": "Branch to run against. Required; ask the user rather than guessing."},
                "note": {"type": "string", "description": "Why this run was booked. Shown in the queue."},
                "allow_protected": {"type": "boolean", "description": "Required to book a publish branch (main, beta, develop, ci-test, alpha-1.0.10). Only set when the user explicitly asked for that branch."},
                **TIME,
            },
            "required": ["ref"],
        },
    },
    {
        "name": "list_schedules",
        "description": "List recurring (cron) schedules. Returns only the cron, timezone and who owns each.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "add_schedule",
        "description": "Add a recurring pipeline schedule. Fires as you, using your token, every time it runs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "cron": {"type": "string", "description": "Cron expression, e.g. '0 2 * * *'."},
                "timezone": {"type": "string", "description": "IANA zone, e.g. Australia/Adelaide. Defaults to UTC."},
                "runner_tag": {"type": "string", "enum": ["perentie-runner", "tern-runner"]},
                "variables": {"type": "object"},
                "ref": {"type": "string"},
                "note": {"type": "string"},
                "allow_protected": {"type": "boolean"},
            },
            "required": ["cron", "ref"],
        },
    },
    {
        "name": "remove_schedule",
        "description": "Remove a recurring schedule by id.",
        "inputSchema": {"type": "object", "properties": {"schedule_id": {"type": "string"}}, "required": ["schedule_id"]},
    },
    {
        "name": "move_booking",
        "description": "Reschedule one of your own bookings that has not started yet.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}, **TIME}, "required": ["run_id"]},
    },
    {
        "name": "cancel_booking",
        "description": "Remove one of your own bookings that has not started. Never touches a running GitLab pipeline.",
        "inputSchema": {"type": "object", "properties": {"run_id": {"type": "string"}}, "required": ["run_id"]},
    },
]


def describe(r, when):
    bits = [f"Booked {r['id']}", f"when: {when}",
            f"ref: {r['ref']} on {r['runner_tag']}, as {r['requested_by']}"]
    if r.get("variables"):
        bits.append("variables: " + ", ".join(f"{k}={v}" for k, v in r["variables"].items()))
    if r.get("note"):
        bits.append(f"note: {r['note']}")
    return "\n".join(bits)


def list_bookings():
    rows = call("GET", "/bookings")["bookings"]
    if not rows:
        return "Nothing booked."
    return "\n".join(f"{b['id']} {(b['at'] or '')[:16]} {b['requested_by']}" for b in rows)


def list_schedules():
    rows = call("GET", "/schedules")["schedules"]
    if not rows:
        return "No recurring schedules."
    return "\n".join(f"{s['id']} {s['cron']} {s['timezone']} {s['requested_by']}" for s in rows)


def add_schedule(cron, ref, timezone="UTC", runner_tag="perentie-runner", variables=None,
                 note="", allow_protected=False):
    body = {"cron": cron, "ref": ref, "timezone": timezone, "runner_tag": runner_tag,
            "variables": variables or {}, "note": note, "allow_protected": allow_protected}
    r = call("POST", "/schedules/add", body)
    return describe(r, f"{r['cron']} ({r['timezone']})")


def remove_schedule(schedule_id):
    return f"Removed {call('POST', '/schedules/remove', {'schedule_id': schedule_id})['removed']}"


def book_pipeline(ref, runner_tag="perentie-runner", variables=None, note="",
                  allow_protected=False, start_in=None, start_at=None):
    body = {"ref": ref, "runner_tag": runner_tag, "variables": variables or {}, "note": note,
            "allow_protected": allow_protected, "scheduled_time": when(start_at, start_in)}
    r = call("POST", "/book", body)
    return describe(r, r["at"])


def move_booking(run_id, start_in=None, start_at=None):
    r = call("POST", "/move", {"run_id": run_id, "scheduled_time": when(start_at, start_in)})
    return f"{r['status']} moved to {r['at']}"


def cancel_booking(run_id):
    return f"Cancelled {call('POST', '/cancel', {'run_id': run_id})['cancelled']}"


HANDLERS = {t["name"]: globals()[t["name"]] for t in TOOLS}


def handle(msg):
    method, mid = msg.get("method"), msg.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "pipeline-scheduler", "version": "2.3.0"},
        }
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            text = HANDLERS[msg["params"]["name"]](**msg["params"].get("arguments", {}))
            result = {"content": [{"type": "text", "text": text}]}
        except Exception as e:
            msg = str(e) if isinstance(e, RuntimeError) else f"{type(e).__name__}: {e}"
            result = {"content": [{"type": "text", "text": msg}], "isError": True}
    elif method == "ping":
        result = {}
    elif mid is None:
        return None
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"unknown method {method}"}}
    return None if mid is None else {"jsonrpc": "2.0", "id": mid, "result": result}


if not TOKEN:
    print(f"pipeline-scheduler MCP: set GITLAB_TOKEN (gate {GATE})", file=sys.stderr)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    reply = handle(json.loads(line))
    if reply:
        print(json.dumps(reply), flush=True)
