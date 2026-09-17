# pipeline-scheduler

Books GitLab E2E pipeline runs so they queue for a runner slot instead of
contending. Two containers: Prefect does the scheduling, a gate in front is the
only thing exposed.

Slot is released when the pipeline stops holding runners (no job in
created/pending/running), not when GitLab calls it finished. Early gate failure
frees the slot immediately; `allow_failure` masking does not keep it held.

## Shape

    LAN ──> gate :8080 ──> prefect :4200 (no published port) ──> gitlab.com

The gate has five handlers and no endpoint that returns a secret. Prefect's API,
including the three routes that return block values in plaintext, is reachable
only from the gate over the compose network.

## Identity

Every request carries the caller's own GitLab PAT as `Authorization: Bearer`.
The gate resolves it against `GET /api/v4/user` and uses that username as
`requested_by`. There is no field to name someone else, so bookings cannot be
forged, and GitLab attributes each pipeline to whoever booked it.

The PAT is stored as a Prefect Secret block (`gitlab-token-<user>`), encrypted
at rest, refreshed on every call so rotation is automatic. It leaves the box
only as a `PRIVATE-TOKEN` header to gitlab.com.

## Install

On the machine that will host the scheduler (one box for the whole team):

    git clone https://github.com/BigTommo/pipeline-scheduler.git
    cd pipeline-scheduler
    cp .env.example .env     # set GITLAB_PROJECT and PREFECT_AUTH
    ./start.sh

Everyone else installs from the running gate, not from here. It serves its own
client, so nobody needs repo access:

    curl http://scheduler-host:8080/install

## Run

    ./start.sh

`./start.sh -f` starts and then follows the logs; Ctrl-C detaches and leaves the
stack running. `./start.sh --ui` also opens Prefect's admin UI on loopback.

`./start.sh` works out this machine's LAN address and advertises it, so the
install strings teammates copy point at this host rather than their own
localhost. Plain `docker compose up -d --build` also works, but then `/install`
echoes back whatever hostname you happened to browse with.
`DRY_RUN` defaults to `true`: bookings run and log, nothing reaches GitLab.
Set `DRY_RUN=false` in `.env` and `docker compose up -d` again to go live.

## Dashboard

`http://scheduler-host:8080/` shows the queue, the recurring schedules, a
click-to-copy one-liner for booking a run right now, and the install commands.
It is read-only otherwise: who holds a slot, when,
and the recurring schedules. Refreshes itself, no login, same minimal fields as
the API. Changing anything goes through the CLI or MCP.

The Prefect UI is deliberately not published. If you need it for internals:

    ./start.sh --ui

That binds it to `127.0.0.1:4200` on the host only. It can hand out stored
tokens, so never bind it to anything else.

## Env

| Var | Default | |
|---|---|---|
| `GITLAB_PROJECT` | required | path or numeric id |
| `PREFECT_AUTH` | required | `user:pass`, internal only, never leaves the box |
| `GATE_PORT` | `8080` | published port |
| `DEFAULT_REF` | `dev/1.0.13` | backstop only; every booking names its own branch |
| `RUNNER_TAGS` | `perentie-runner,tern-runner` | one concurrency limit each, value 1 |
| `PROTECTED_REFS` | `main,beta,develop,ci-test,alpha-1.0.10` | refused without `allow_protected` |
| `POLL_SECONDS` | `30` | |
| `DRY_RUN` | `true` | |
| `SLACK_WEBHOOK_URL` | unset | alerts on any failed or crashed booking |

## Using it

Set your own PAT and point at the gate:

    export GITLAB_TOKEN=glpat-xxx
    export SCHEDULER_URL=http://scheduler-host:8080

    ./book.py list
    ./book.py book --ref dev/1.0.13 --in 2h --var RUN_CYPRESS_TESTS=true --var "CYPRESS_STAGES=setup & priority"
    ./book.py book --ref dev/1.0.13 --at "2026-09-18 02:00" --tag tern-runner
    ./book.py move <run-id> --in 90m
    ./book.py cancel <run-id>

Recurring schedules:

    ./book.py schedules
    ./book.py schedule '0 2 * * *' --ref dev/1.0.13 --tz Australia/Adelaide --var RUN_CYPRESS_TESTS=true
    ./book.py unschedule <schedule-id>

`list`, `schedules`, `move`, `cancel` and `unschedule` need no token. `book` and
`schedule` need yours, because they fire CI as you. Publish branches are refused
at book time, not an hour later when the run fires.

## Agents

One line, on each person's own machine. `curl http://scheduler-host:8080/install`
prints these with the right host filled in:

    curl -sfO http://scheduler-host:8080/client/mcp_server.py && claude mcp add pipeline-scheduler --scope user -e GITLAB_TOKEN=glpat-xxx -e SCHEDULER_URL=http://scheduler-host:8080 -- python3 "$PWD/mcp_server.py"

`--scope user` registers it once for every project. The VS Code Claude extension
reads the same config as the terminal, so this one command covers both; restart
the extension to pick it up. Do not use VS Code's own `.vscode/mcp.json`, which
is Copilot's, not Claude's.

The skill is optional and also one line:

    mkdir -p ~/.claude/skills/pipeline-book && curl -sf http://scheduler-host:8080/client/SKILL.md -o ~/.claude/skills/pipeline-book/SKILL.md

Or from GitHub, for anyone without the gate in reach:

    curl -sfO https://raw.githubusercontent.com/BigTommo/pipeline-scheduler/main/mcp_server.py

Prefer the gate: what it serves always matches the server that is running, while
a GitHub copy can drift ahead of the deployment.

`mcp_server.py` is a single self-contained file, stdlib only. No clone, nothing
to `pip install`.

Then ask for things in plain language: "book the priority suite for 2am",
"what's queued", "push my 2am one back three hours".

Your PAT is read from the MCP process's own environment. It is never a tool
argument, so it never enters the model's context, and there is nothing to
redact. The tools expose no identity field at all, so an agent cannot book on
anyone else's behalf.

The skill carries the judgement (book instead of trigger when a
run is in flight, name whose booking you are about to move), while the MCP
carries the mechanism.

## Gate API

A PAT is required only where one will eventually trigger CI. A slot is a shared
team resource, so anyone can see the queue, move it, or give it back.

| | Endpoint | PAT |
|---|---|---|
| | `GET /bookings` | no |
| | `GET /schedules` | no |
| | `POST /move` `{run_id, scheduled_time}` | no |
| | `POST /cancel` `{run_id}` | no |
| | `POST /schedules/remove` `{schedule_id}` | no |
| | `POST /book` `{ref, scheduled_time?, variables?, runner_tag?, note?, allow_protected?}` | **yes** |
| | `POST /schedules/add` `{ref, cron, timezone?, ...same}` | **yes** |
| | `GET /`, `GET /install`, `GET /client/{mcp_server.py,book.py,SKILL.md}` | no |

Anything else is 404.

`scheduled_time` is optional and defaults to now; pass an ISO UTC timestamp to
book for later. `runner_tag` must be one of `RUNNER_TAGS`: an unknown tag has no
concurrency limit behind it and would skip the queue entirely.

`ref` is required on both. It is payload for GitLab, not a scheduling input:
queueing is keyed on `runner_tag` alone, so two bookings on different branches
still contend for the same slot. The project is fixed per deployment, in env.

Both listings return **only the time, the person, and an id to act on**. Branch,
runner tag, variables and notes are never returned, so the queue shows who holds
a slot and when, and nothing about what they are running.

## Threat model

Nothing here relies on the code being secret. What it relies on:

- Prefect publishes no port. The three API routes that return block values in
  plaintext are reachable only from the gate, over the compose network.
- The gate has a fixed set of handlers and no endpoint that returns a secret.
- Identity is GitLab's answer to the caller's own PAT, not a claim in the body.
- `PREFECT_AUTH` is a real secret and lives only in `.env`, never in the repo.

**The gate must stay on a trusted network.** `move`, `cancel` and
`schedules/remove` need no credential by design, because a slot is a shared team
resource. Exposed to the internet, that is an open queue-wipe endpoint. Bind it
to the office LAN or a VPN, never a public IP.

## Recurring schedules survive restarts

`serve()` re-registers the deployment every time the container starts, and that
drops whatever schedules the deployment had. So the gate keeps its own copy in
`/data/schedules.json` and re-applies any that are missing, on every read and
every 20 seconds. Without it, one restart silently deleted every nightly.

Re-applied schedules get a new id, so an id you noted earlier can go stale after
a restart. Look it up again with `./book.py schedules`.

## Known gaps

- Anyone who gets a shell in either container, or onto the Docker network, can
  read every stored PAT. Network isolation is the control, not Prefect.
- No egress restriction yet: the Prefect container can reach the internet, not
  only gitlab.com.
- A container restart mid-run orphans the slot and loses the watcher.
- Nothing stops a pipeline triggered outside the scheduler from taking the
  runner. A `resource_group` in `.gitlab-ci.yml` is the only thing that would.
