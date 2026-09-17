# Commands

## Stack

    cp .env.example .env            # first time: set GITLAB_PROJECT, PREFECT_AUTH
    docker compose up -d --build    # start (and restart after an .env change)
    docker compose ps
    docker compose logs -f prefect
    docker compose logs -f gate
    docker compose restart gate
    docker compose stop
    docker compose down             # remove containers, keep bookings
    docker compose down -v          # also wipe the volume

Changing `.env` needs `docker compose up -d` again.

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

    curl -sfO http://scheduler-host:8080/client/mcp_server.py && claude mcp add pipeline-scheduler -e GITLAB_TOKEN=glpat-xxx -e SCHEDULER_URL=http://scheduler-host:8080 -- python3 "$PWD/mcp_server.py"

Skill:

    mkdir -p ~/.claude/skills/pipeline-book && curl -sf http://scheduler-host:8080/client/SKILL.md -o ~/.claude/skills/pipeline-book/SKILL.md
