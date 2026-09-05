# Critical Path Monitor

A workflow engine for datacenter rack buildouts that answers a question most
orchestrators don't:

> When a task gets blocked, is that block **free**, or is it **costing delivery
> time right now**?

Airflow and Temporal will tell you *that* something is blocked. Neither tells
you what the block *costs*. That gap is the whole point of this tool.

## The idea

Every task has a duration, so every task has a **buffer**: the number of minutes
it can sit blocked before the job as a whole finishes late. A task with 140
minutes of buffer can be blocked for two hours and nothing bad happens. A task
with zero buffer is on the **critical path**, and blocking it costs delivery
time from the first minute.

The engine derives both from the DAG using the two classic critical-path passes,
then projects them against what is actually happening right now.

## Try it

The demo seeds itself on startup, so there is nothing to set up. Two buildouts
run side by side.

**The whole product in two clicks.** Open a compute rack job and:

1. Block **Network fabric pull** (140m buffer). Its buffer bar visibly drains,
   and the job's delay stays at **0**. The block is free.
2. Reset, then block **Power whip install** (0m buffer, on the critical path).
   Delay starts climbing immediately, minute for minute.

Same gesture, opposite outcomes. That contrast is the thing worth looking at.

Also worth trying: block every runnable task and watch the job go `blocked` and
hand its concurrency slot back to the fleet, then unblock it and watch it jump
to the front of the queue.

## Run locally

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync
PYTHONPATH=. uv run uvicorn app.main:app --port 8000
# open http://localhost:8000
```

Tests:

```bash
PYTHONPATH=. uv run pytest -q
```

## API

```
POST   /api/jobs                                    {"workflow": "compute_rack"|"storage_rack"}
GET    /api/jobs
GET    /api/jobs/{job_id}                           snapshot + DAG edges
POST   /api/jobs/{job_id}/tasks/{task_id}/block     409 unless the task is pending
POST   /api/jobs/{job_id}/tasks/{task_id}/unblock
POST   /api/demo/run                                reset and reseed
GET    /api/state?since={seq}                       config + jobs + new events
```

## How it fits together

| File | Role |
|---|---|
| `app/critical_path_evaluator.py` | The two CPM passes, buffer, live projection |
| `app/scheduler.py` | Job/task queues, worker pool, block/unblock |
| `app/workflows.py` | The two buildout DAGs |
| `app/models.py` | Domain models |
| `app/main.py` | HTTP surface |
| `static/` | Single-page UI, vanilla JS + SVG |

A few decisions worth knowing:

- **The baseline is derived once from the DAG and then never moves.** The
  backward pass targets it rather than the live projection. If the target slid
  forward with every slip, buffer would be permanently zero and the delay figure
  would always read `+0m`.
- **Job state is eventually consistent.** A reconciler recomputes cached
  snapshots every 250ms and reads serve the cache, so no request walks the DAG.
  Block and unblock force an immediate recompute so the one interaction a person
  actually performs feels instant.
- **The scheduler holds no locks**, deliberately. Every read-modify-write section
  is await-free, so cooperative scheduling makes it atomic. This is documented at
  the top of `app/scheduler.py`, because adding an `await` in the wrong place
  would quietly break it.
- **Only pending tasks can be blocked.** In-flight work runs to completion, which
  keeps the state machine small. The consequence is that a job reaches `blocked`
  only once its last running task drains, so it settles rather than flipping.

## Known limitations

The worker pool is a simulation dial rather than a real constraint — buildout
capacity is treated as unlimited in practice, which is what lets critical path
analysis apply cleanly. Starving the pool by setting `MAX_CONCURRENT_TASKS` low
will show up as delay the analysis did not predict.

Tasks cannot fail or be retried; the only interruption modelled is an external
block. Delay is reported but not attributed to a cause. State is in memory only,
so a restart reseeds the demo rather than recovering.
