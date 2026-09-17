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

## Booking

    export GITLAB_TOKEN=glpat-xxx
    export SCHEDULER_URL=http://localhost:8080

    ./book.py list
    ./book.py book --in 2h --var RUN_BUILD=true --var RUN_UNIT_TESTS=false
    ./book.py book --at "2026-09-18 02:00" --tag tern-runner
    ./book.py move <run-id> --in 90m
    ./book.py cancel <run-id>

## Recurring schedules

    ./book.py schedules
    ./book.py schedule '0 2 * * *' --tz Australia/Adelaide --var RUN_CYPRESS_TESTS=true
    ./book.py unschedule <schedule-id>

Only `book` and `schedule` need `GITLAB_TOKEN`.

## Wiring agents

    ln -s $PWD/skill ~/.claude/skills/pipeline-book
    claude mcp add pipeline-scheduler -e GITLAB_TOKEN=glpat-xxx \
      -e SCHEDULER_URL=http://localhost:8080 -- python3 $PWD/mcp_server.py
