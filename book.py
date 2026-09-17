#!/usr/bin/env python3
"""Book, list, move and cancel pipeline runs. Identity comes from your own
GitLab PAT in GITLAB_TOKEN; there is no way to book as someone else."""
import argparse
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
        sys.exit(json.loads(e.read() or '{"error":"?"}').get("error", e.reason))
    except urllib.error.URLError as e:
        sys.exit(f"scheduler unreachable at {GATE}: {e.reason}")


def when(at, delay):
    if delay:
        n, unit = int(re.match(r"(\d+)", delay).group(1)), delay[-1]
        t = datetime.now(timezone.utc) + timedelta(minutes=n * {"m": 1, "h": 60, "d": 1440}[unit])
    elif at:
        t = datetime.fromisoformat(at).astimezone(timezone.utc)
    else:
        t = datetime.now(timezone.utc)
    return t.isoformat().replace("+00:00", "Z")


def book(a):
    body = {
        "variables": dict(v.split("=", 1) for v in a.var),
        "runner_tag": a.tag,
        "note": a.note,
        "allow_protected": a.allow_protected,
        "scheduled_time": when(a.at, getattr(a, "in")),
    }
    if a.ref:
        body["ref"] = a.ref
    r = call("POST", "/book", body)
    print(r["id"], r["name"], r["at"])


def ls(a):
    for b in call("GET", "/bookings")["bookings"]:
        print(b["id"], (b["at"] or "")[:16], b["requested_by"])


def schedules(a):
    for s in call("GET", "/schedules")["schedules"]:
        print(s["id"], s["cron"].ljust(14), (s["timezone"] or "").ljust(20), s["requested_by"])


def add_schedule(a):
    body = {
        "cron": a.cron,
        "timezone": a.tz,
        "variables": dict(v.split("=", 1) for v in a.var),
        "runner_tag": a.tag,
        "note": a.note,
        "allow_protected": a.allow_protected,
    }
    if a.ref:
        body["ref"] = a.ref
    r = call("POST", "/schedules/add", body)
    print(r["id"], r["cron"], r["requested_by"])


def drop_schedule(a):
    print("removed", call("POST", "/schedules/remove", {"schedule_id": a.id})["removed"])


def move(a):
    r = call("POST", "/move", {"run_id": a.id, "scheduled_time": when(a.at, getattr(a, "in"))})
    print(r["status"], r["at"])


def cancel(a):
    print("cancelled", call("POST", "/cancel", {"run_id": a.id})["cancelled"])


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(required=True)

    b = sub.add_parser("book")
    b.add_argument("--tag", default="perentie-runner")
    b.add_argument("--ref")
    b.add_argument("--var", action="append", default=[])
    b.add_argument("--note", default="")
    b.add_argument("--allow-protected", action="store_true")
    b.add_argument("--at")
    b.add_argument("--in", dest="in")
    b.set_defaults(func=book)

    sub.add_parser("list").set_defaults(func=ls)
    sub.add_parser("schedules").set_defaults(func=schedules)

    sa = sub.add_parser("schedule")
    sa.add_argument("cron", help="e.g. '0 2 * * *'")
    sa.add_argument("--tz", default="UTC")
    sa.add_argument("--tag", default="perentie-runner")
    sa.add_argument("--ref")
    sa.add_argument("--var", action="append", default=[])
    sa.add_argument("--note", default="")
    sa.add_argument("--allow-protected", action="store_true")
    sa.set_defaults(func=add_schedule)

    su = sub.add_parser("unschedule")
    su.add_argument("id")
    su.set_defaults(func=drop_schedule)

    m = sub.add_parser("move")
    m.add_argument("id")
    m.add_argument("--at")
    m.add_argument("--in", dest="in")
    m.set_defaults(func=move)

    c = sub.add_parser("cancel")
    c.add_argument("id")
    c.set_defaults(func=cancel)

    a = p.parse_args()
    if a.func in (book, add_schedule) and not TOKEN:
        sys.exit("set GITLAB_TOKEN: booking triggers CI as you, so it needs your own token")
    a.func(a)


if __name__ == "__main__":
    main()
