import os
import time
from urllib.parse import quote

import httpx
from prefect import flow, get_run_logger, serve
from prefect.blocks.system import Secret
from prefect.concurrency.sync import concurrency

GITLAB = os.getenv("GITLAB_URL", "https://gitlab.com").rstrip("/")
PROJECT = os.environ["GITLAB_PROJECT"]
POLL = int(os.getenv("POLL_SECONDS", "30"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
DEFAULT_REF = os.getenv("DEFAULT_REF", "dev/1.0.13")
RUNNER_TAGS = [t.strip() for t in os.getenv("RUNNER_TAGS", "perentie-runner,tern-runner").split(",")]
PROTECTED = {r.strip() for r in os.getenv("PROTECTED_REFS", "main,beta,develop,ci-test,alpha-1.0.10").split(",")}
SLACK = os.getenv("SLACK_WEBHOOK_URL")

ACTIVE = {"created", "pending", "running"}
BASE = f"{GITLAB}/api/v4/projects/{quote(PROJECT, safe='')}"


def api(method, path, token, **kw):
    r = httpx.request(method, BASE + path, headers={"PRIVATE-TOKEN": token}, timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def notify(text):
    if SLACK:
        httpx.post(SLACK, json={"text": text}, timeout=10)


def holds_runners(pipeline_id, token):
    jobs = api("GET", f"/pipelines/{pipeline_id}/jobs", token, params={"per_page": 100})
    return any(j["status"] in ACTIVE for j in jobs)


def on_bad(flow, flow_run, state):
    who = (flow_run.parameters or {}).get("requested_by", "?")
    notify(f":x: booking {flow_run.name} ({who}) {state.type.value.lower()}: {state.message}")


@flow(name="run-pipeline", log_prints=True, on_failure=[on_bad], on_crashed=[on_bad])
def run_pipeline(
    ref: str = DEFAULT_REF,
    variables: dict | None = None,
    runner_tag: str = RUNNER_TAGS[0],
    requested_by: str = "",
    note: str = "",
    allow_protected: bool = False,
):
    log = get_run_logger()
    if not requested_by:
        raise ValueError("requested_by is required")
    if ref in PROTECTED and not allow_protected:
        raise ValueError(f"{ref} is a publish branch; book it with allow_protected=true if you mean it")

    vars = dict(variables or {})
    vars["RUNNER_TAG"] = runner_tag
    vars["SCHEDULER_REQUESTED_BY"] = requested_by
    vars["SCHEDULER_LABEL"] = f"booked by {requested_by}" + (f" ({note})" if note else "")

    with concurrency(runner_tag):
        if DRY_RUN:
            log.info("DRY_RUN, would trigger %s for %s: %s", ref, requested_by, vars)
            return {"ref": ref, "requested_by": requested_by, "variables": vars}

        token = Secret.load(f"gitlab-token-{requested_by}").get()
        pipeline = api("POST", "/pipeline", token, json={
            "ref": ref,
            "variables": [{"key": k, "value": str(v)} for k, v in vars.items()],
        })
        log.info("started %s", pipeline["web_url"])

        while holds_runners(pipeline["id"], token):
            time.sleep(POLL)

        status = api("GET", f"/pipelines/{pipeline['id']}", token)["status"]
        log.info("runners idle, status=%s", status)
        if status != "success":
            raise RuntimeError(f"pipeline {status} on {ref}: {pipeline['web_url']}")
        return {"ref": ref, "url": pipeline["web_url"], "status": status, "requested_by": requested_by}


if __name__ == "__main__":
    serve(run_pipeline.to_deployment(name="e2e", tags=["e2e"]))
