# Commands

Everything in one place. `scheduler-host:8080` below is whatever
`curl http://<host>:<port>/install` prints.

## First run

    cp .env.example .env    # set GITLAB_PROJECT and PREFECT_AUTH
    ./start.sh

`DRY_RUN` defaults to `true`: bookings run and log, nothing reaches GitLab.
Set `DRY_RUN=false` in `.env` and `./start.sh` again to go live.

## Stack

    ./start.sh              start, advertising this machine's LAN address
    ./start.sh -f           start, then follow logs
    ./start.sh --ui         also expose Prefect's admin UI on 127.0.0.1
    ./start.sh --ui -f      both

    docker compose ps
    docker compose logs -f prefect
    docker compose logs -f gate
    docker compose stop
    docker compose down             remove containers, keep the volume
    docker compose down -v          also wipe bookings and schedules

Any `.env` change needs `./start.sh` again.

Three things that catch people:

- **Repeat both `-f` files on every compose command once you use `--ui`.** A
  plain `docker compose up -d` recreates prefect without the override and
  silently closes the admin UI.
- **Rebuilding either service recreates both**, since they share an image. That
  restarts prefect, which crashes any run in flight.
- Ctrl-C during `-f` is only safe once the URLs have printed. Interrupting the
  build leaves the stack down.

## Admin UI

    ./start.sh --ui         then http://127.0.0.1:4200

Prefect has no login form. The browser prompts for HTTP Basic; enter your whole
`PREFECT_AUTH` string (`user:pass`) as the username. Loopback only, deliberately:
this is the interface that can hand back stored tokens.

## Dashboard

    http://scheduler-host:8080/

Queue, recent runs, recurring schedules, copy-button commands, payload fields.
Read-only. Anyone on the LAN can open it, no token needed.

## Booking

    export GITLAB_TOKEN=glpat-your-own-token
    export SCHEDULER_URL=http://scheduler-host:8080

    ./book.py list
    ./book.py book --ref dev/1.0.13 --in 2h --var RUN_BUILD=true --var RUN_UNIT_TESTS=false
    ./book.py book --ref dev/1.0.13 --at "2026-09-18 02:00" --tag tern-runner --note "nightly"
    ./book.py move <run-id> --in 90m
    ./book.py cancel <run-id>

`--in` takes `30m`/`2h`/`1d`, `--at` takes local time, omit both to run as soon
as a slot frees. `--ref` is required. Only `book` and `schedule` need a token.

## Recurring schedules

    ./book.py schedules
    ./book.py schedule '0 2 * * *' --ref dev/1.0.13 --tz Australia/Adelaide --var RUN_CYPRESS_TESTS=true
    ./book.py unschedule <schedule-id>

Re-applied schedules get a new id after a restart; look it up again rather than
saving one.

## Raw API

    GET  /bookings                 no token
    GET  /schedules                no token
    GET  /recent                   no token
    GET  /fields                   no token, lists every payload key
    POST /move     {run_id, scheduled_time}          no token
    POST /cancel   {run_id}                          no token
    POST /schedules/remove {schedule_id}             no token
    POST /book     {ref, ...}                        your PAT
    POST /schedules/add {ref, cron, ...}             your PAT

Book one right now:

    curl -sf -X POST http://scheduler-host:8080/book \
      -H "Authorization: Bearer $GITLAB_TOKEN" -H 'Content-Type: application/json' \
      -d '{"ref":"dev/1.0.13","variables":{"RUN_BUILD":"true"}}'

Add `"scheduled_time":"2026-09-18T02:00:00Z"` (UTC) to book for later.
`curl http://scheduler-host:8080/fields` lists every key and its default.

## Wiring agents

    curl http://scheduler-host:8080/install        prints all three, host filled in

MCP:

    mkdir -p ~/.claude/mcp/pipeline-scheduler && curl -sf http://scheduler-host:8080/client/mcp_server.py -o ~/.claude/mcp/pipeline-scheduler/mcp_server.py && claude mcp add pipeline-scheduler --scope user -e GITLAB_TOKEN=glpat-xxx -e SCHEDULER_URL=http://scheduler-host:8080 -- python3 ~/.claude/mcp/pipeline-scheduler/mcp_server.py

Skill:

    mkdir -p ~/.claude/skills/pipeline-book && curl -sf http://scheduler-host:8080/client/SKILL.md -o ~/.claude/skills/pipeline-book/SKILL.md

CLI only:

    mkdir -p ~/.local/bin && curl -sf http://scheduler-host:8080/client/book.py -o ~/.local/bin/book.py && chmod +x ~/.local/bin/book.py

`--scope user` matters: the default `local` binds the MCP to one directory. The
VS Code Claude extension reads the same config, so this covers both; restart it
afterwards. `.vscode/mcp.json` is Copilot's, not Claude's.

To upgrade, re-run the `curl` alone. The registration already points at the path,
and Claude launches that file every session, so it cannot be deleted.

## Clearing state

    ./reset.sh          wipe bookings, runs and stored tokens; keep schedules
    ./reset.sh --all    also drop recurring schedules
    docker compose down -v      nuclear, takes the volume too

Tokens are stored again on each person's next booking, so nobody reinstalls.

## When something looks stuck

    docker compose exec prefect prefect global-concurrency-limit ls

`active=1` with nothing running means a slot leaked; restarting prefect clears
it and says so in the log. Bookings that had not started are requeued; ones that
were mid-run are crashed, because they may already have triggered a pipeline.

    docker compose exec prefect printenv | grep DRY_RUN

Worth checking before a test booking. `false` means the next booking starts a
real pipeline.
