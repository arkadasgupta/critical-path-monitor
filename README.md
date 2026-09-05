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

## Known limitation

Critical-path analysis assumes unlimited workers, so buffer is an **upper
bound**: it cannot *predict* delay caused by contention for the worker pool.
It does, however, *measure* it, because a task that is ready but has no free
worker gets floored at the current time and its projection slides. Predicting
contention would need a resource-constrained scheduling simulation, which is out
of scope here.

State is in memory only. A restart reseeds the demo rather than recovering.
