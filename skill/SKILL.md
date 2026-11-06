---
name: pipeline-book
description: Book, reschedule, list or cancel a GitLab E2E pipeline run on the shared office scheduler, so runs queue for a runner slot instead of contending. Use when asked to schedule, book, defer, move, or queue a pipeline for later, or to see what pipelines are already booked. For an immediate unqueued pipeline, use ci-trigger instead.
---

# Booking a pipeline run

The scheduler is a Prefect container on `http://localhost:4200`, separate from
the repo. It holds one slot per runner tag, so a booked run waits rather than
contending with a run already in flight.

`book.py` lives in `/home/tom-tern/Documents/projects/pipeline-scheduler`.

## When this skill, when ci-trigger

| Ask | Use |
|---|---|
| "run the E2E suite" (now, don't care about queueing) | `ci-trigger` |
| "run it tonight" / "after the current one finishes" / "at 2am" | this skill |
| "what's queued" / "move mine later" / "cancel my booking" | this skill |

If a run is already in flight and the user wants another, booking is almost
always the right answer even when they said "run it now" — say so, book it, and
tell them it will start when the slot frees.

## Commands

    ./book.py list
    ./book.py book --ref <branch> --in 2h --var RUN_CYPRESS_TESTS=true --var "CYPRESS_STAGES=setup & priority"
    ./book.py book --ref <branch> --at "2026-09-18 02:00" --tag tern-runner
    ./book.py move <run-id> --in 90m
    ./book.py cancel <run-id>
    ./book.py schedules
    ./book.py schedule '0 2 * * *' --ref <branch> --tz Australia/Adelaide --var RUN_CYPRESS_TESTS=true
    ./book.py unschedule <schedule-id>

Identity comes from the caller's own GitLab PAT, so there is no way to book for
someone else and nothing to pass. `--ref` is required: ask which branch rather
than assuming the one that happens to be checked out. `--in` takes
`30m`/`2h`/`1d`; `--at` takes local time. Omit both to queue immediately.
`--tag` is `perentie-runner` (default) or `tern-runner`. `--ref` overrides the
branch.

`--input` values are typed GitLab pipeline inputs (`true`/`false` and integers
are typed, the rest are strings); `--var` values are pipeline variables, always
strings. Use `--input` for anything in the `spec:inputs:` block at the top of
`.gitlab-ci.yml` in the paratoo-fdcp repo. The config reads inputs when it
compiles, so a variable cannot change shard counts (`PRIORITY_PARALLEL`,
`RUN_PARALLEL`), `ISOLATED_STACKS`/`DISTRIBUTION` runner tags or the pipeline
name. `RUNNER_TAG` is set from `--tag`, and GitLab rejects a tag missing from
that input's `options:`. Read the real names and defaults from that block; do
not trust a doc for them.

    ./book.py book --ref <branch> --at "2026-11-07 10:00" --tag huy-runner \
      --input RUN_CYPRESS_TESTS=true --input DISTRIBUTION=pull \
      --input ISOLATED_STACKS=distributed --input PRIORITY_PARALLEL=10 --input RUN_PARALLEL=16

If the `pipeline-scheduler` MCP tools are available, prefer them over `book.py`.
Same four verbs, typed arguments.

## Rules

- Always `list` before booking. If the same thing is already queued, say so
  rather than adding a duplicate.
- A slot is a shared team resource: anyone may move or cancel any booking. Before
  moving or cancelling one you did not make, say whose it is and ask first.
- Listings return only the time, the person and an id. If asked what a booked run
  actually does, say that the queue does not expose it rather than guessing.
- Show the resolved command and get a go-ahead before booking, unless the user
  said to go ahead. Booking is cheap but it consumes a slot other people wait on.
- This never cancels a running GitLab pipeline. `cancel` only removes a booking
  that has not started.
- If the container is unreachable, say so plainly. Do not fall back to
  `ci-trigger` to "get it done" — that bypasses the queue.

## Reporting back

Give the run id, the tag, and when it is expected to start. If the slot is
busy, say what it is waiting behind.
