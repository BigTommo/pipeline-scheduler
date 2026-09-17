# Commands

## Stack

    cp .env.example .env    # first time: set GITLAB_PROJECT, PREFECT_AUTH
    ./start.sh              # start, advertising this machine's LAN address
    ./start.sh -f           # start, then follow logs (Ctrl-C leaves it running)
    ./start.sh --ui         # also expose Prefect's admin UI on 127.0.0.1
    docker compose ps
    docker compose logs -f prefect
    docker compose logs -f gate
    docker compose restart gate
    docker compose stop
    docker compose down             # remove containers, keep bookings
    docker compose down -v          # also wipe the volume

Changing `.env` needs `docker compose up -d` again.

## Clearing the database

    ./reset.sh          wipe bookings, runs and stored tokens; keep schedules
    ./reset.sh --all    also drop recurring schedules

Asks for confirmation. Tokens are re-stored on each person's next booking, so
nobody has to reinstall anything.

Nuclear alternative, also destroys the volume and `schedules.json`:

    docker compose down -v

## Dashboard

    http://localhost:8080/          read-only queue view

Prefect's own UI, loopback only, when you need internals:

    docker compose -f docker-compose.yml -f docker-compose.ui.yml up -d

## Booking

    export GITLAB_TOKEN=glpat-xxx
    export SCHEDULER_URL=http://localhost:8080

    ./book.py list
    ./book.py book --ref dev/1.0.13 --in 2h --var RUN_BUILD=true --var RUN_UNIT_TESTS=false
    ./book.py book --ref dev/1.0.13 --at "2026-09-18 02:00" --tag tern-runner
    ./book.py move <run-id> --in 90m
    ./book.py cancel <run-id>

## Recurring schedules

    ./book.py schedules
    ./book.py schedule '0 2 * * *' --ref dev/1.0.13 --tz Australia/Adelaide --var RUN_CYPRESS_TESTS=true
    ./book.py unschedule <schedule-id>

Only `book` and `schedule` need `GITLAB_TOKEN`.

## Wiring agents

    curl http://scheduler-host:8080/install        # prints the one-liners

MCP:

    mkdir -p ~/.claude/mcp/pipeline-scheduler && curl -sf http://scheduler-host:8080/client/mcp_server.py -o ~/.claude/mcp/pipeline-scheduler/mcp_server.py && claude mcp add pipeline-scheduler --scope user -e GITLAB_TOKEN=glpat-xxx -e SCHEDULER_URL=http://scheduler-host:8080 -- python3 ~/.claude/mcp/pipeline-scheduler/mcp_server.py

Book one right now, no scheduling:

    curl -sf -X POST http://scheduler-host:8080/book -H "Authorization: Bearer $GITLAB_TOKEN" \
      -H 'Content-Type: application/json' \
      -d '{"ref":"dev/1.0.13","variables":{"RUN_BUILD":"true"}}'

Skill:

    mkdir -p ~/.claude/skills/pipeline-book && curl -sf http://scheduler-host:8080/client/SKILL.md -o ~/.claude/skills/pipeline-book/SKILL.md
