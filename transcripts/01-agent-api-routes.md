# Subagent — API routes and demo seeding

Verbatim record of the Claude Code session that built this project, converted from the raw JSONL log. Model reasoning ("thinking") blocks are omitted; very long tool outputs are truncated with the original length noted.

---


---

## ‹User› · 23:10:31 UTC

Write `app/main.py` and `app/demo.py` for an existing FastAPI project at /Users/arkadasgupta/work/critical-path-monitor.

DO NOT MODIFY any existing file. Read them first: app/scheduler.py, app/models.py, app/critical_path_evaluator.py, app/events.py, app/config.py, app/workflows.py. Only create app/main.py and app/demo.py.

Run things with: `cd /Users/arkadasgupta/work/critical-path-monitor && PYTHONPATH=. uv run ...`

## What already exists

`app.scheduler.Engine` — async, no locks:
- `await engine.run()` — long-running; supervises dispatcher + reconciler + worker pool via asyncio.TaskGroup. Run it as a background task.
- `await engine.create_job(workflow_id: str, name: str | None) -> JobSnapshot` — raises `KeyError` for unknown workflow_id, `JobLimitReached` at capacity.
- `await engine.block_task(job_id, task_id) -> JobSnapshot` — raises `KeyError` if job/task unknown, `TaskNotBlockable` if the task is not pending.
- `await engine.unblock_task(job_id, task_id) -> JobSnapshot` — raises `KeyError`, or `TaskNotBlocked` if not blocked.
- `engine.jobs: dict[str, Job]`, `engine.snapshots: dict[str, JobSnapshot]`
- `engine.events` — `.seq: int`, `.since(seq: int) -> list[Event]`
Exceptions importable from app.scheduler: `JobLimitReached`, `TaskNotBlockable`, `TaskNotBlocked`.

`app.config.as_dict() -> dict[str,int]` with keys max_jobs, max_concurrent_jobs, max_concurrent_tasks, tick_ms, reconcile_interval_ms.

`app.workflows.WORKFLOWS: dict[str, WorkflowDef]` with ids `compute_rack` and `storage_rack`. A WorkflowDef has `.id`, `.name`, `.task_defs` (tuple of TaskDef with `.id`, `.label`, `.duration_min`, `.parent_task_ids`), `.baseline_finish_min`.

Pydantic models (already serializable):
- JobSnapshot: job_id, name, workflow_id, workflow_name, state, baseline_finish_min, projected_finish_min, delay_incurred_min, elapsed_min, tasks (tuple of TaskView), computed_at_min, snapshot_seq
- TaskView: id, label, duration_min, state, stalled, startable, ls, lf, buffer_min, on_critical_path, es, ef, buffer_remaining_min
- Event: seq, at_min, message, job_id

## Exact API contract — implement precisely, no extra endpoints

```
POST   /api/jobs                 body {"workflow": str, "name": str|null}  -> JobSnapshot
                                 404 unknown workflow, 429 JobLimitReached
GET    /api/jobs                 -> list[JobSnapshot]
GET    /api/jobs/{job_id}        -> JobSnapshot fields PLUS "edges": [{"source": task_id, "target": task_id}]
                                 404 unknown job
POST   /api/jobs/{job_id}/tasks/{task_id}/block    -> JobSnapshot; 404 unknown; 409 TaskNotBlockable
POST   /api/jobs/{job_id}/tasks/{task_id}/unblock  -> JobSnapshot; 404 unknown; 409 TaskNotBlocked
POST   /api/demo/run             -> {"ok": true, "jobs": list[JobSnapshot]}
GET    /api/state?since=<int>    -> {"config": {...}, "jobs": [JobSnapshot], "events": [Event], "seq": int}
```
`since` defaults to 0. `seq` in the response is `engine.events.seq`. `events` is `engine.events.since(since)`.
Build `edges` from the workflow definition: for each task_def, for each parent in `task_def.parent_task_ids`, an edge {"source": parent, "target": task_def.id}.

Use a pydantic request model for the create-job body. Define response models where useful, but JobSnapshot/Event are already pydantic and can be returned directly.

## Engine lifecycle and reset

Use a FastAPI `lifespan` context manager. Store the engine on `app.state.engine` and the runner task on `app.state.runner`. Routes MUST read `request.app.state.engine` on each call, never capture it at import time — because /api/demo/run replaces the engine.

Reset semantics for POST /api/demo/run: cancel `app.state.runner`, await its cancellation (suppress CancelledError), construct a fresh `Engine()`, store it on app.state, start a new runner task, then seed. Replacing the object is the reset — do not try to drain queues or reset the semaphore in place.

On shutdown, cancel the runner and suppress CancelledError.

## app/demo.py

Expose `async def seed(engine) -> list[JobSnapshot]`. It should create one `compute_rack` job and one `storage_rack` job with readable names (e.g. "compute-rack-01", "storage-rack-01"). Keep it simple — do NOT pre-block anything; the reviewer does that interactively. Call `seed` once at startup from the lifespan so a cold start lands on a live running demo, and again from /api/demo/run.

## Static files

Mount `static/` at the root path so `/` serves `static/index.html`: `app.mount("/", StaticFiles(directory="static", html=True), name="static")`. Mount it AFTER all /api routes are registered. The static/ directory is being written by someone else in parallel — create it with a placeholder `index.html` if missing so the app still boots, but do not write any real frontend.

## Verify before you finish

Start the server (`PYTHONPATH=. uv run uvicorn app.main:app --port 8000`), then with curl confirm: /api/state returns config+jobs+events; two jobs are auto-seeded and progressing; /api/jobs/{id} includes edges; blocking a pending task returns 200 and blocking a running one returns 409; /api/demo/run resets and reseeds. Kill the server when done.

Report: the files you created and the actual curl output proving each endpoint works.

---

## ‹Claude› · 23:10:33 UTC

I'll start by reading the existing files to understand the codebase.

---

## ‹Claude› · 23:10:34 UTC

**→ Bash**


*List project structure*


```bash
ls -la && echo "---" && ls -la app/ 2>/dev/null && echo "--- static ---" && ls -la static/ 2>/dev/null
```

---

## ‹User› · 23:10:34 UTC

<details><summary>result</summary>

```
Exit code 1
total 336
drwxr-xr-x   7 arkadasgupta  staff     224 Sep  4 15:30 .
drwxr-xr-x   8 arkadasgupta  staff     256 Sep  4 14:33 ..
drwxr-xr-x@  3 arkadasgupta  staff      96 Sep  4 15:52 .claude
drwxr-xr-x@  8 arkadasgupta  staff     256 Sep  4 15:29 .venv
drwxr-xr-x@ 10 arkadasgupta  staff     320 Sep  4 16:08 app
-rw-r--r--@  1 arkadasgupta  staff     507 Sep  4 15:29 pyproject.toml
-rw-r--r--@  1 arkadasgupta  staff  165195 Sep  4 15:29 uv.lock
---
total 88
drwxr-xr-x@ 10 arkadasgupta  staff    320 Sep  4 16:08 .
drwxr-xr-x   7 arkadasgupta  staff    224 Sep  4 15:30 ..
-rw-r--r--@  1 arkadasgupta  staff      0 Sep  4 15:51 __init__.py
drwxr-xr-x@  9 arkadasgupta  staff    288 Sep  4 16:09 __pycache__
-rw-r--r--@  1 arkadasgupta  staff   1804 Sep  4 15:30 config.py
-rw-r--r--@  1 arkadasgupta  staff   7964 Sep  4 15:51 critical_path_evaluator.py
-rw-r--r--@  1 arkadasgupta  staff    968 Sep  4 15:56 events.py
-rw-r--r--@  1 arkadasgupta  staff   5279 Sep  4 15:46 models.py
-rw-r--r--@  1 arkadasgupta  staff  10268 Sep  4 16:08 scheduler.py
-rw-r--r--@  1 arkadasgupta  staff   4173 Sep  4 15:52 workflows.py
--- static ---
```
</details>

---

## ‹Claude› · 23:10:35 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/app/scheduler.py`


---

## ‹User› · 23:10:35 UTC

<details><summary>result</summary>

```
1	"""In-memory job and task scheduler.
2	
3	Task concurrency is capped by the size of the worker pool draining a single
4	global task queue. The queue is global rather than per-job so that concurrent
5	jobs contend with each other, which is what makes MAX_CONCURRENT_TASKS
6	observable.
7	
8	There are deliberately no locks. Every section that reads and then mutates
9	shared state contains no await, so under cooperative scheduling it cannot
10	interleave. Queue puts always happen after the mutation, never inside it. If you
11	add an await mid-mutation, that invariant breaks and you will need a lock - so
12	don't, or say why in a comment.
13	
14	The one thing that genuinely changes underneath us is a task being blocked while
15	it sits in the task queue. Workers re-check task state on dequeue rather than
16	trusting that a queued task is still runnable.
17	"""
18	
19	import asyncio
20	import time
21	import uuid
22	from collections import deque
23	
24	from app import config
25	from app.critical_path_evaluator import compute_snapshot
26	from app.events import EventLog
27	from app.models import Job, JobSnapshot, JobState, Task, TaskState
28	from app.workflows import WORKFLOWS
29	
30	_ORIGIN = time.monotonic()
31	
32	
33	def now_min() -> float:
34	    return (time.monotonic() - _ORIGIN) * 1000.0 / config.TICK_MS
35	
36	
37	def _minutes_to_seconds(minutes: float) -> float:
38	    return minutes * config.TICK_MS / 1000.0
39	
40	
41	class JobLimitReached(Exception):
42	    pass
43	
44	
45	class TaskNotBlockable(Exception):
46	    pass
47	
48	
49	class TaskNotBlocked(Exception):
50	    pass
51	
52	
53	class Engine:
54	    def __init__(self) -> None:
55	        self.jobs: dict[str, Job] = {}
56	        self.snapshots: dict[str, JobSnapshot] = {}
57	        self.events = EventLog()
58	
59	        # A deque rather than an asyncio.Queue because an unblocked job goes to
60	        # the front: it already waited once and should not queue again behind
61	        # jobs that were admitted while it was stuck.
62	        self._job_queue: deque[str] = deque()
63	        self._job_available = asyncio.Event()
64	
65	        self._task_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
66	        self._enqueued: set[tuple[str, str]] = set()
67	        self._job_slots = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)
68	        self._admitted: set[str] = set()
69	        self._snapshot_seq = 0
70	
71	    async def run(self) -> None:
72	        async with asyncio.TaskGroup() as g
… truncated (11223 chars total)
```
</details>

---

## ‹Claude› · 23:10:37 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/app/models.py`


---

## ‹User› · 23:10:37 UTC

<details><summary>result</summary>

```
1	"""Domain models.
2	
3	Time convention: Task times are relative to job start, Job times are absolute
4	since service boot. Both in simulated minutes. Task times are relative so they
5	compare directly against the CPM numbers, which also start at 0.
6	"""
7	
8	from enum import Enum
9	
10	from pydantic import BaseModel, ConfigDict, Field
11	
12	
13	class TaskState(str, Enum):
14	    PENDING = "pending"
15	    RUNNING = "running"
16	    BLOCKED = "blocked"
17	    FINISHED = "finished"
18	
19	
20	class JobState(str, Enum):
21	    WAITING = "waiting"
22	    RUNNING = "running"
23	    BLOCKED = "blocked"
24	    FINISHED = "finished"
25	
26	
27	class TaskDef(BaseModel):
28	    model_config = ConfigDict(frozen=True)
29	
30	    id: str
31	    label: str
32	    duration_min: int = Field(gt=0)
33	    parent_task_ids: tuple[str, ...] = ()
34	
35	
36	class StaticSchedule(BaseModel):
37	    """CPM figures that depend only on the DAG, durations, and the baseline.
38	
39	    None of these change while a job runs, so they are computed once per
40	    workflow rather than once per reconcile. Keeping them static is also what
41	    stops the critical path from flickering between reconciles.
42	    """
43	
44	    model_config = ConfigDict(frozen=True)
45	
46	    es: int
47	    ef: int
48	    ls: int
49	    lf: int
50	    buffer_min: int
51	    on_critical_path: bool
52	
53	
54	class WorkflowDef(BaseModel):
55	    """Build via criticality.build_workflow, which populates the derived fields
56	    below. Constructing this directly leaves it without a static schedule."""
57	
58	    id: str
59	    name: str
60	    task_defs: tuple[TaskDef, ...]
61	
62	    baseline_finish_min: int = 0
63	    task_defs_by_id: dict[str, TaskDef] = Field(default_factory=dict, repr=False)
64	    child_task_ids: dict[str, tuple[str, ...]] = Field(
65	        default_factory=dict, repr=False
66	    )
67	    topo_order: tuple[str, ...] = Field(default=(), repr=False)
68	    static: dict[str, StaticSchedule] = Field(default_factory=dict, repr=False)
69	
70	    def model_post_init(self, __context: object) -> None:
71	        task_defs_by_id = {t.id: t for t in self.task_defs}
72	        if len(task_defs_by_id) != len(self.task_defs):
73	            raise ValueError(f"workflow {self.id} has duplicate task ids")
74	        for t in self.task_defs:
75	            for p in t.parent_task_ids:
76	                if p not in task_defs_by_id:
77	                    raise ValueError(f"task {t.id} references unknow
… truncated (5906 chars total)
```
</details>

---

## ‹Claude› · 23:10:37 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/app/workflows.py`


---

## ‹Claude› · 23:10:37 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/app/config.py`


---

## ‹User› · 23:10:37 UTC

<details><summary>result</summary>

```
1	"""The two supported buildout workflows.
2	
3	Both share the same site-preparation skeleton and diverge at the point where the
4	rack is populated, which gives them genuinely different critical paths rather
5	than being the same DAG with renamed tasks.
6	
7	Durations are in simulated minutes.
8	"""
9	
10	from app.critical_path_evaluator import build_workflow
11	from app.models import TaskDef, WorkflowDef
12	
13	# Site preparation, identical for both buildout types.
14	_SHARED_PREP = (
15	    TaskDef(id="site_survey", label="Site survey", duration_min=30),
16	    TaskDef(id="rack_delivery", label="Rack delivery", duration_min=60),
17	    TaskDef(
18	        id="rack_placement",
19	        label="Rack placement",
20	        duration_min=45,
21	        parent_task_ids=("site_survey", "rack_delivery"),
22	    ),
23	    TaskDef(
24	        id="power_whip_install",
25	        label="Power whip install",
26	        duration_min=90,
27	        parent_task_ids=("site_survey",),
28	    ),
29	    TaskDef(
30	        id="network_fabric_pull",
31	        label="Network fabric pull",
32	        duration_min=120,
33	        parent_task_ids=("site_survey",),
34	    ),
35	    TaskDef(
36	        id="pdu_install",
37	        label="PDU install",
38	        duration_min=40,
39	        parent_task_ids=("rack_placement", "power_whip_install"),
40	    ),
41	    TaskDef(
42	        id="tor_switch_install",
43	        label="ToR switch install",
44	        duration_min=50,
45	        parent_task_ids=("rack_placement", "network_fabric_pull"),
46	    ),
47	)
48	
49	COMPUTE_RACK = build_workflow(
50	    "compute_rack",
51	    "Compute rack buildout",
52	    _SHARED_PREP
53	    + (
54	        TaskDef(
55	            id="gpu_node_install",
56	            label="GPU node install",
57	            duration_min=180,
58	            parent_task_ids=("rack_placement", "pdu_install"),
59	        ),
60	        TaskDef(
61	            id="node_cabling",
62	            label="Node cabling",
63	            duration_min=70,
64	            parent_task_ids=("gpu_node_install", "tor_switch_install"),
65	        ),
66	        TaskDef(
67	            id="firmware_flash",
68	            label="Firmware flash",
69	            duration_min=60,
70	            parent_task_ids=("node_cabling",),
71	        ),
72	        TaskDef(
73	            id="burn_in_test",
74	            label="Burn-in test",
75	            duration_min=150,
76	            parent_task_ids=("firmware_flash",),
77	        ),
78
… truncated (4652 chars total)
```
</details>

---

## ‹User› · 23:10:37 UTC

<details><summary>result</summary>

```
1	"""Deployment-time constants.
2	
3	There is deliberately no config API. These are read once at import and surfaced
4	read-only through /api/state so the UI can display them.
5	"""
6	
7	import os
8	
9	
10	def _int_env(name: str, default: int) -> int:
11	    raw = os.environ.get(name)
12	    if raw is None:
13	        return default
14	    try:
15	        value = int(raw)
16	    except ValueError as exc:
17	        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
18	    if value < 1:
19	        raise ValueError(f"{name} must be >= 1, got {value}")
20	    return value
21	
22	
23	# Admission cap on total jobs held in memory. Creating beyond this returns 429.
24	MAX_JOBS = _int_env("MAX_JOBS", 8)
25	
26	# How many jobs may be admitted from the job queue at once.
27	MAX_CONCURRENT_JOBS = _int_env("MAX_CONCURRENT_JOBS", 2)
28	
29	# Size of the global task worker pool. Global rather than per-job on purpose:
30	# this is what makes two concurrent jobs contend, and it is the reason two
31	# workflow types exist rather than one. Kept low so the knob is visibly
32	# load-bearing instead of decorative.
33	MAX_CONCURRENT_TASKS = _int_env("MAX_CONCURRENT_TASKS", 3)
34	
35	# Real milliseconds per simulated minute. A 640-minute compute buildout
36	# completes in ~32s, storage in ~42s. Reviewers will not wait four real minutes.
37	TICK_MS = _int_env("TICK_MS", 50)
38	
39	# How often the reconciler recomputes job snapshots. Job state is eventually
40	# consistent with this as the staleness bound.
41	RECONCILE_INTERVAL_MS = _int_env("RECONCILE_INTERVAL_MS", 250)
42	
43	
44	def as_dict() -> dict[str, int]:
45	    """Read-only view for the /api/state payload."""
46	    return {
47	        "max_jobs": MAX_JOBS,
48	        "max_concurrent_jobs": MAX_CONCURRENT_JOBS,
49	        "max_concurrent_tasks": MAX_CONCURRENT_TASKS,
50	        "tick_ms": TICK_MS,
51	        "reconcile_interval_ms": RECONCILE_INTERVAL_MS,
52	    }
53
```
</details>

---

## ‹Claude› · 23:10:38 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/app/events.py`


---

## ‹User› · 23:10:38 UTC

<details><summary>result</summary>

```
1	"""Append-only event log with a monotonic sequence number.
2	
3	The UI polls with the last seq it saw rather than holding a socket open.
4	"""
5	
6	from collections import deque
7	
8	from pydantic import BaseModel, ConfigDict
9	
10	
11	class Event(BaseModel):
12	    model_config = ConfigDict(frozen=True)
13	
14	    seq: int
15	    at_min: float
16	    message: str
17	    job_id: str | None = None
18	
19	
20	class EventLog:
21	    def __init__(self, capacity: int = 500) -> None:
22	        self._events: deque[Event] = deque(maxlen=capacity)
23	        self._seq = 0
24	
25	    @property
26	    def seq(self) -> int:
27	        return self._seq
28	
29	    def emit(self, at_min: float, message: str, job_id: str | None = None) -> None:
30	        self._seq += 1
31	        self._events.append(
32	            Event(seq=self._seq, at_min=at_min, message=message, job_id=job_id)
33	        )
34	
35	    def since(self, seq: int) -> list[Event]:
36	        return [e for e in self._events if e.seq > seq]
37	
38	    def clear(self) -> None:
39	        self._events.clear()
40
```
</details>

---

## ‹Claude› · 23:10:38 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/pyproject.toml`


---

## ‹User› · 23:10:39 UTC

<details><summary>result</summary>

```
1	[project]
2	name = "critical-path-monitor"
3	version = "0.1.0"
4	description = "Workflow engine that tells you what a blocked task actually costs"
5	requires-python = ">=3.11"
6	dependencies = [
7	    "fastapi>=0.115",
8	    "uvicorn[standard]>=0.32",
9	    "pydantic>=2.9",
10	]
11	
12	[dependency-groups]
13	dev = [
14	    "pytest>=8.3",
15	    "httpx>=0.27",
16	]
17	
18	[tool.pytest.ini_options]
19	testpaths = ["tests"]
20	
21	[build-system]
22	requires = ["hatchling"]
23	build-backend = "hatchling.build"
24	
25	[tool.hatch.build.targets.wheel]
26	packages = ["app"]
27
```
</details>

---

## ‹Claude› · 23:10:52 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/app/critical_path_evaluator.py`


---

## ‹User› · 23:10:52 UTC

<details><summary>result</summary>

```
1	"""Critical path evaluation.
2	
3	The static pass (baseline, LS/LF, buffer, critical path) runs once per workflow
4	at import. The live pass (ES/EF, projection, delay) runs each reconcile against
5	actual task state.
6	
7	The backward pass targets the baseline derived at import, never the live
8	projection. Targeting the live projection would slide the deadline forward with
9	every slip, buffer would be permanently zero, and delay_incurred_min would
10	always read 0.
11	"""
12	
13	from app.models import (
14	    Job,
15	    JobSnapshot,
16	    JobState,
17	    StaticSchedule,
18	    TaskDef,
19	    TaskState,
20	    TaskView,
21	    WorkflowDef,
22	)
23	
24	
25	def build_workflow(
26	    workflow_id: str, name: str, task_defs: tuple[TaskDef, ...]
27	) -> WorkflowDef:
28	    """The sanctioned constructor: builds the DAG and attaches its schedule."""
29	    workflow = WorkflowDef(id=workflow_id, name=name, task_defs=task_defs)
30	    schedule, baseline = _static_pass(workflow)
31	    workflow.baseline_finish_min = baseline
32	    workflow.static = schedule
33	    return workflow
34	
35	
36	def _static_pass(workflow: WorkflowDef) -> tuple[dict[str, StaticSchedule], int]:
37	    # Forward pass: how early can each task possibly happen?
38	    #
39	    # A task cannot start until every one of its parents has finished, so its
40	    # earliest start is the LATEST of its parents' earliest finishes - the
41	    # slowest parent is the one that gates it. Tasks with no parents start at 0.
42	    #
43	    # max() rather than sum() is the whole point: sibling branches run in
44	    # parallel, so two parents taking 60 and 90 minutes gate the child at 90,
45	    # not 150. Walking in topological order guarantees every parent's EF is
46	    # already known by the time we reach the child.
47	    es: dict[str, int] = {}
48	    ef: dict[str, int] = {}
49	    for task_id in workflow.topo_order:
50	        parents = workflow.parents(task_id)
51	        es[task_id] = max((ef[p] for p in parents), default=0)
52	        ef[task_id] = es[task_id] + workflow.duration(task_id)
53	
54	    # The job is done when its slowest chain is done. This is the baseline: the
55	    # delivery commitment every later measurement is taken against.
56	    baseline = max(ef.values())
57	
58	    # Backward pass: how late can each task happen without moving that finish?
59	    #
60	    # The mirror image. A task must be finished before its children need to
61	    # start, so its latest finish is the
… truncated (8659 chars total)
```
</details>

---

## ‹Claude› · 23:10:53 UTC

Now I'll write the two files.

---

## ‹Claude› · 23:10:58 UTC

**→ Write**


`/Users/arkadasgupta/work/critical-path-monitor/app/demo.py`


```
"""Demo seeding.

One compute buildout and one storage buildout, which is exactly enough to make
the shared config knobs visible: MAX_CONCURRENT_JOBS admits both, and the two
workflows then contend for the same MAX_CONCURRENT_TASKS worker pool.

Nothing is pre-blocked on purpose. Blocking is the one interaction worth
performing by hand, and a seeded block would spend it before anyone looks.
"""

from app.models import JobSnapshot

SEEDS: tuple[tuple[str, str], ...] = (
    ("compute_rack", "compute-rack-01"),
    ("storage_rack", "storage-rack-01"),
)


async def seed(engine) -> list[JobSnapshot]:
    """Create the demo jobs on a fresh engine and return their snapshots."""
    return [
        await engine.create_job(workflow_id, name) for workflow_id, name in SEEDS
    ]
```

---

## ‹User› · 23:11:21 UTC

<details><summary>result</summary>

```
File created successfully at: /Users/arkadasgupta/work/critical-path-monitor/app/demo.py (file state is current in your context — no need to Read it back)
```
</details>

---

## ‹Claude› · 23:11:25 UTC

**→ Edit**


`/Users/arkadasgupta/work/critical-path-monitor/app/demo.py`


```
from app.models import JobSnapshot
from app.scheduler import Engine

SEEDS
```

---

## ‹User› · 23:11:31 UTC

<details><summary>result</summary>

```
The file /Users/arkadasgupta/work/critical-path-monitor/app/demo.py has been updated successfully. (file state is current in your context — no need to Read it back)
```
</details>

---

## ‹Claude› · 23:11:33 UTC

**→ Edit**


`/Users/arkadasgupta/work/critical-path-monitor/app/demo.py`


```
async def seed(engine: Engine) -> list[JobSnapshot]:
```

---

## ‹User› · 23:11:39 UTC

<details><summary>result</summary>

```
The file /Users/arkadasgupta/work/critical-path-monitor/app/demo.py has been updated successfully. (file state is current in your context — no need to Read it back)
```
</details>

---

## ‹Claude› · 23:12:04 UTC

**→ Write**


`/Users/arkadasgupta/work/critical-path-monitor/app/main.py`


```
"""HTTP surface over the scheduler.

The engine is process state, not module state: it lives on `app.state.engine`
and every route reads it from the request. That indirection exists because
POST /api/demo/run resets by replacing the Engine object outright - a route
that captured the engine at import would keep serving the dead one.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, HTTPException, Path as PathParam, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import config, demo
from app.events import Event
from app.models import JobSnapshot
from app.scheduler import Engine, JobLimitReached, TaskNotBlockable, TaskNotBlocked
from app.workflows import WORKFLOWS

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

_PLACEHOLDER_INDEX = """<!doctype html>
<meta charset="utf-8">
<title>Critical Path Monitor</title>
<p>API is up. The UI has not been built yet - try
<a href="/api/state">/api/state</a>.</p>
"""


def _ensure_static_dir() -> None:
    """StaticFiles refuses to mount a missing directory, and the frontend is
    being written in parallel. Never overwrites a real index.html."""
    STATIC_DIR.mkdir(exist_ok=True)
    index = STATIC_DIR / "index.html"
    if not index.exists():
        index.write_text(_PLACEHOLDER_INDEX)


class CreateJobRequest(BaseModel):
    workflow: str
    name: str | None = None


class Edge(BaseModel):
    source: str
    target: str


class JobDetail(JobSnapshot):
    """A snapshot plus the workflow DAG, so the UI can draw the graph from a
    single request instead of hard-coding the topology."""

    edges: tuple[Edge, ...]


class DemoRunResponse(BaseModel):
    ok: bool = True
… truncated
```

---

## ‹User› · 23:13:02 UTC

<details><summary>result</summary>

```
File created successfully at: /Users/arkadasgupta/work/critical-path-monitor/app/main.py (file state is current in your context — no need to Read it back)
```
</details>

---

## ‹Claude› · 23:13:08 UTC

**→ Edit**


`/Users/arkadasgupta/work/critical-path-monitor/app/main.py`


```
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config, demo
from app.events import Event
from app.models import JobSnapshot
from app.scheduler import Engine, JobLimitReached, TaskNotBlockable, TaskNotBlocked
```

---

## ‹User› · 23:13:19 UTC

<details><summary>result</summary>

```
The file /Users/arkadasgupta/work/critical-path-monitor/app/main.py has been updated successfully. (file state is current in your context — no need to Read it back)
```
</details>

---

## ‹Claude› · 23:13:21 UTC

**→ Edit**


`/Users/arkadasgupta/work/critical-path-monitor/app/main.py`


```
@app.post("/api/jobs", response_model=JobSnapshot)
```

---

## ‹User› · 23:13:26 UTC

<details><summary>result</summary>

```
The file /Users/arkadasgupta/work/critical-path-monitor/app/main.py has been updated successfully. (file state is current in your context — no need to Read it back)
```
</details>

---

## ‹Claude› · 23:13:30 UTC

**→ Edit**


`/Users/arkadasgupta/work/critical-path-monitor/app/main.py`


```
    return [
        engine.snapshots[job_id]
        for job_id in engine.jobs
        if job_id in engine.snapshots
    ]


@app.get("/api/jobs/{job_id}", response_model=JobDetail)
async def get_job(request: Request, job_id: str) -> JobDetail:
```

---

## ‹User› · 23:13:40 UTC

<details><summary>result</summary>

```
The file /Users/arkadasgupta/work/critical-path-monitor/app/main.py has been updated successfully. (file state is current in your context — no need to Read it back)
```
</details>

---

## ‹Claude› · 23:13:41 UTC

Now let me start the server and verify.

---

## ‹Claude› · 23:13:44 UTC

**→ Bash**


*Start uvicorn server in background*


```bash
PYTHONPATH=. nohup uv run uvicorn app.main:app --port 8000 > /private/tmp/claude-501/-Users-arkadasgupta-work-critical-path-monitor/414c2a7c-83a4-4916-97b1-fc38c5fd5c81/scratchpad/server.log 2>&1 &
echo started; sleep 6; tail -20 /private/tmp/claude-501/-Users-arkadasgupta-work-critical-path-monitor/414c2a7c-83a4-4916-97b1-fc38c5fd5c81/scratchpad/server.log
```

---

## ‹User› · 23:13:56 UTC

<details><summary>result</summary>

```
started
INFO:     Started server process [92442]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```
</details>

---

## ‹Claude› · 23:13:59 UTC

**→ Bash**


*Check /api/state*


```bash
echo "=== GET /api/state (truncated) ===" && curl -s "http://127.0.0.1:8000/api/state" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('config:', json.dumps(d['config']))
print('seq:', d['seq'])
print('jobs:', [(j['name'], j['state'], round(j['elapsed_min'],1), round(j['projected_finish_min'],1), j['baseline_finish_min']) for j in d['jobs']])
print('events (first 6):')
for e in d['events'][:6]: print('  ', e)
print('total events:', len(d['events']))
"
```

---

## ‹User› · 23:14:01 UTC

<details><summary>result</summary>

```
=== GET /api/state (truncated) ===
config: {"max_jobs": 8, "max_concurrent_jobs": 2, "max_concurrent_tasks": 3, "tick_ms": 50, "reconcile_interval_ms": 250}
seq: 21
jobs: [('compute-rack-01', 'running', 196.2, 745.1, 640), ('storage-rack-01', 'running', 196.2, 945.1, 840)]
events (first 6):
   {'seq': 1, 'at_min': 10.784019159618765, 'message': 'job compute-rack-01 queued', 'job_id': '0012c7af'}
   {'seq': 2, 'at_min': 10.787024999735877, 'message': 'job storage-rack-01 queued', 'job_id': '100360de'}
   {'seq': 3, 'at_min': 10.887139160186052, 'message': 'job compute-rack-01 admitted', 'job_id': '0012c7af'}
   {'seq': 4, 'at_min': 10.888419159455225, 'message': 'job storage-rack-01 admitted', 'job_id': '100360de'}
   {'seq': 5, 'at_min': 10.889694159850478, 'message': 'site_survey started', 'job_id': '0012c7af'}
   {'seq': 6, 'at_min': 10.890530840260908, 'message': 'rack_delivery started', 'job_id': '0012c7af'}
total events: 21
```
</details>

---

## ‹Claude› · 23:14:08 UTC

**→ Bash**


*Inspect job detail with edges*


```bash
cd /Users/arkadasgupta/work/critical-path-monitor
JOB=$(curl -s http://127.0.0.1:8000/api/jobs | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['job_id'])")
echo "job_id=$JOB"
echo "=== GET /api/jobs/$JOB ==="
curl -s "http://127.0.0.1:8000/api/jobs/$JOB" | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('top-level keys:', list(d.keys()))
print('edges:', json.dumps(d['edges']))
print('tasks:', [(t['id'], t['state'], t['on_critical_path'], round(t['buffer_remaining_min'],1)) for t in d['tasks']])
"
echo "=== 404 unknown job ==="
curl -s -o /dev/stderr -w "status=%{http_code}\n" http://127.0.0.1:8000/api/jobs/nope; echo
```

---

## ‹User› · 23:14:09 UTC

<details><summary>result</summary>

```
job_id=0012c7af
=== GET /api/jobs/0012c7af ===
top-level keys: ['job_id', 'name', 'workflow_id', 'workflow_name', 'state', 'baseline_finish_min', 'projected_finish_min', 'delay_incurred_min', 'elapsed_min', 'tasks', 'computed_at_min', 'snapshot_seq', 'edges']
edges: [{"source": "site_survey", "target": "rack_placement"}, {"source": "rack_delivery", "target": "rack_placement"}, {"source": "site_survey", "target": "power_whip_install"}, {"source": "site_survey", "target": "network_fabric_pull"}, {"source": "rack_placement", "target": "pdu_install"}, {"source": "power_whip_install", "target": "pdu_install"}, {"source": "rack_placement", "target": "tor_switch_install"}, {"source": "network_fabric_pull", "target": "tor_switch_install"}, {"source": "rack_placement", "target": "gpu_node_install"}, {"source": "pdu_install", "target": "gpu_node_install"}, {"source": "gpu_node_install", "target": "node_cabling"}, {"source": "tor_switch_install", "target": "node_cabling"}, {"source": "node_cabling", "target": "firmware_flash"}, {"source": "firmware_flash", "target": "burn_in_test"}, {"source": "node_cabling", "target": "network_validation"}, {"source": "burn_in_test", "target": "capacity_handoff"}, {"source": "network_validation", "target": "capacity_handoff"}]
tasks: [('site_survey', 'finished', True, -0.0), ('rack_delivery', 'finished', False, 15.0), ('rack_placement', 'finished', False, -105.1), ('power_whip_install', 'finished', True, -0.0), ('network_fabric_pull', 'finished', False, 110.0), ('pdu_install', 'finished', True, -105.1), ('tor_switch_install', 'finished', False, 64.9), ('gpu_node_install', 'running', True, -115.1), ('node_cabling', 'pending', True, -115.1), ('firmware_flash', 'pending', True, -115.1), ('burn_in_test', 'pending', True, -115.1), ('network_validation', 'pending', False, 49.9), ('capacity_handoff', 'pending', True, -115.1)]
=== 404 unknown job ===
{"detail":"unknown job nope"}status=404
```
</details>

---

## ‹Claude› · 23:14:17 UTC

**→ Bash**


*Test demo reset endpoint*


```bash
cd /Users/arkadasgupta/work/critical-path-monitor
echo "=== POST /api/demo/run (reset) ==="
curl -s -w "\nstatus=%{http_code}\n" -X POST http://127.0.0.1:8000/api/demo/run | python3 -c "
import sys,json
raw=sys.stdin.read()
body,status=raw.rsplit('\n',2)[0], raw.strip().splitlines()[-1]
d=json.loads(body)
print(status)
print('ok:', d['ok'])
print('jobs:', [(j['job_id'], j['name'], j['workflow_id'], j['state'], j['elapsed_min']) for j in d['jobs']])
"
echo "=== jobs after reset (fresh ids, count) ==="
curl -s http://127.0.0.1:8000/api/jobs | python3 -c "import json,sys; d=json.load(sys.stdin); print(len(d), [(j['job_id'],j['name'],j['state']) for j in d])"
echo "=== seq restarted (events log is new engine) ==="
curl -s "http://127.0.0.1:8000/api/state?since=0" | python3 -c "import json,sys; d=json.load(sys.stdin); print('seq:',d['seq'],'events:',len(d['events']))"
```

---

## ‹User› · 23:14:20 UTC

<details><summary>result</summary>

```
=== POST /api/demo/run (reset) ===
status=200
ok: True
jobs: [('d1446414', 'compute-rack-01', 'compute_rack', 'waiting', 0.0), ('b65ee64a', 'storage-rack-01', 'storage_rack', 'waiting', 0.0)]
=== jobs after reset (fresh ids, count) ===
2 [('d1446414', 'compute-rack-01', 'running'), ('b65ee64a', 'storage-rack-01', 'running')]
=== seq restarted (events log is new engine) ===
seq: 7 events: 7
```
</details>

---

## ‹Claude› · 23:14:31 UTC

**→ Bash**


*Test block/unblock endpoints*


```bash
cd /Users/arkadasgupta/work/critical-path-monitor
curl -s -X POST http://127.0.0.1:8000/api/demo/run > /dev/null
JOB=$(curl -s http://127.0.0.1:8000/api/jobs | python3 -c "import json,sys; print(json.load(sys.stdin)[0]['job_id'])")
echo "job=$JOB"
echo "--- state of tasks right now ---"
curl -s http://127.0.0.1:8000/api/jobs/$JOB | python3 -c "import json,sys; print([(t['id'],t['state']) for t in json.load(sys.stdin)['tasks']])"

echo
echo "=== BLOCK pending task burn_in_test ==="
curl -s -w "\nstatus=%{http_code}\n" -X POST http://127.0.0.1:8000/api/jobs/$JOB/tasks/burn_in_test/block | python3 -c "
import sys,json
raw=sys.stdin.read(); status=raw.strip().splitlines()[-1]
d=json.loads(raw.rsplit('\n',2)[0])
print(status)
print('job state:', d['state'], 'projected:', round(d['projected_finish_min'],1), 'delay:', round(d['delay_incurred_min'],1))
print('burn_in_test:', [t for t in d['tasks'] if t['id']=='burn_in_test'][0]['state'])
print('capacity_handoff stalled:', [t for t in d['tasks'] if t['id']=='capacity_handoff'][0]['stalled'])
"

echo
echo "=== BLOCK a RUNNING task (expect 409) ==="
RUNNING=$(curl -s http://127.0.0.1:8000/api/jobs/$JOB | python3 -c "
import json,sys
d=json.load(sys.stdin)
r=[t['id'] for t in d['tasks'] if t['state']=='running']
print(r[0] if r else '')
")
echo "running task = $RUNNING"
curl -s -w "\nstatus=%{http_code}\n" -X POST http://127.0.0.1:8000/api/jobs/$JOB/tasks/$RUNNING/block

echo
echo "=== UNBLOCK burn_in_test (expect 200) ==="
curl -s -w "\nstatus=%{http_code}\n" -X POST http://127.0.0.1:8000/api/jobs/$JOB/tasks/burn_in_test/unblock | python3 -c "
import sys,json
raw=sys.stdin.read(); print(raw.strip().splitlines()[-1])
d=json.loads(raw.rsplit('\n',2)[0])
print('burn_in_test:', [t for t in d['tasks'] if t['id']=='burn_in_test'][0]['st
… truncated
```

---

## ‹User› · 23:14:34 UTC

<details><summary>result</summary>

```
job=4a55567b
--- state of tasks right now ---
[('site_survey', 'running'), ('rack_delivery', 'running'), ('rack_placement', 'pending'), ('power_whip_install', 'pending'), ('network_fabric_pull', 'pending'), ('pdu_install', 'pending'), ('tor_switch_install', 'pending'), ('gpu_node_install', 'pending'), ('node_cabling', 'pending'), ('firmware_flash', 'pending'), ('burn_in_test', 'pending'), ('network_validation', 'pending'), ('capacity_handoff', 'pending')]

=== BLOCK pending task burn_in_test ===
status=200
job state: running projected: 640.0 delay: 0.0
burn_in_test: blocked
capacity_handoff stalled: True

=== BLOCK a RUNNING task (expect 409) ===
running task = site_survey
{"detail":"site_survey is running; only pending tasks can be blocked"}
status=409

=== UNBLOCK burn_in_test (expect 200) ===
status=200
burn_in_test: pending
=== UNBLOCK again (expect 409 TaskNotBlocked) ===
{"detail":"burn_in_test is pending, not blocked"}
status=409
=== block unknown task (expect 404) ===
{"detail":"unknown job 4a55567b or task nope"}
status=404
```
</details>

---

## ‹Claude› · 23:14:45 UTC

**→ Bash**


*Test create job, limits, state since, static*


```bash
cd /Users/arkadasgupta/work/critical-path-monitor
echo "=== POST /api/jobs (valid) ==="
curl -s -w "\nstatus=%{http_code}\n" -X POST http://127.0.0.1:8000/api/jobs -H 'content-type: application/json' -d '{"workflow":"storage_rack","name":"storage-rack-99"}' | python3 -c "
import sys,json
raw=sys.stdin.read(); print(raw.strip().splitlines()[-1])
d=json.loads(raw.rsplit('\n',2)[0])
print({k:d[k] for k in ('job_id','name','workflow_id','workflow_name','state','baseline_finish_min')})
"
echo "=== POST /api/jobs unknown workflow (expect 404) ==="
curl -s -w "\nstatus=%{http_code}\n" -X POST http://127.0.0.1:8000/api/jobs -H 'content-type: application/json' -d '{"workflow":"nope","name":null}'
echo "=== fill to MAX_JOBS=8 then expect 429 ==="
for i in 1 2 3 4 5 6; do
  curl -s -o /dev/null -w "create $i -> %{http_code}\n" -X POST http://127.0.0.1:8000/api/jobs -H 'content-type: application/json' -d '{"workflow":"compute_rack","name":null}'
done
curl -s -w "\nstatus=%{http_code}\n" -X POST http://127.0.0.1:8000/api/jobs -H 'content-type: application/json' -d '{"workflow":"compute_rack","name":null}'
echo "=== /api/state?since=<seq> returns only newer events ==="
SEQ=$(curl -s http://127.0.0.1:8000/api/state | python3 -c "import json,sys; print(json.load(sys.stdin)['seq'])")
echo "seq now $SEQ"
curl -s "http://127.0.0.1:8000/api/state?since=$SEQ" | python3 -c "
import json,sys; d=json.load(sys.stdin)
print('jobs:',len(d['jobs']),'events since seq:',len(d['events']),'new seq:',d['seq'])
print('sample new events:', [e['message'] for e in d['events'][:4]])
"
echo "=== GET / (static) ==="
curl -s -w "\nstatus=%{http_code}\n" http://127.0.0.1:8000/ | head -8
ls -la static/
```

---

## ‹User› · 23:14:47 UTC

<details><summary>result</summary>

```
=== POST /api/jobs (valid) ===
status=200
{'job_id': '3d4f1666', 'name': 'storage-rack-99', 'workflow_id': 'storage_rack', 'workflow_name': 'Storage rack buildout', 'state': 'waiting', 'baseline_finish_min': 840}
=== POST /api/jobs unknown workflow (expect 404) ===
{"detail":"unknown workflow nope"}
status=404
=== fill to MAX_JOBS=8 then expect 429 ===
create 1 -> 200
create 2 -> 200
create 3 -> 200
create 4 -> 200
create 5 -> 200
create 6 -> 429
{"detail":"at capacity: 8/8 jobs"}
status=429
=== /api/state?since=<seq> returns only newer events ===
seq now 35
jobs: 8 events since seq: 0 new seq: 35
sample new events: []
=== GET / (static) ===
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Critical Path Monitor</title>
<link rel="stylesheet" href="/style.css">
</head>
total 48
drwxr-xr-x@ 4 arkadasgupta  staff    128 Sep  4 16:13 .
drwxr-xr-x  9 arkadasgupta  staff    288 Sep  4 16:14 ..
-rw-r--r--@ 1 arkadasgupta  staff   4459 Sep  4 16:12 index.html
-rw-r--r--@ 1 arkadasgupta  staff  15416 Sep  4 16:13 style.css
```
</details>

---

## ‹Claude› · 23:14:59 UTC

**→ Bash**


*Verify reset and progression*


```bash
cd /Users/arkadasgupta/work/critical-path-monitor
echo "=== demo/run resets 8 jobs back to 2 ==="
curl -s -X POST http://127.0.0.1:8000/api/demo/run | python3 -c "import json,sys; d=json.load(sys.stdin); print('ok:',d['ok'],'jobs:',[(j['name'],j['state']) for j in d['jobs']])"
curl -s http://127.0.0.1:8000/api/jobs | python3 -c "import json,sys; print('job count after reset:', len(json.load(sys.stdin)))"
sleep 8
echo "=== progressing 8s later ==="
curl -s http://127.0.0.1:8000/api/state | python3 -c "
import json,sys; d=json.load(sys.stdin)
for j in d['jobs']: print(j['name'], j['state'], 'elapsed', round(j['elapsed_min'],1), 'projected', round(j['projected_finish_min'],1), 'finished tasks', sum(1 for t in j['tasks'] if t['state']=='finished'))
print('seq', d['seq'])
"
echo "=== server log tail (no errors expected) ==="
tail -5 /private/tmp/claude-501/-Users-arkadasgupta-work-critical-path-monitor/414c2a7c-83a4-4916-97b1-fc38c5fd5c81/scratchpad/server.log
grep -ci "traceback\|error" /private/tmp/claude-501/-Users-arkadasgupta-work-critical-path-monitor/414c2a7c-83a4-4916-97b1-fc38c5fd5c81/scratchpad/server.log
```

---

## ‹User› · 23:15:08 UTC

<details><summary>result</summary>

```
=== demo/run resets 8 jobs back to 2 ===
ok: True jobs: [('compute-rack-01', 'waiting'), ('storage-rack-01', 'waiting')]
job count after reset: 2
=== progressing 8s later ===
compute-rack-01 running elapsed 161.0 projected 726.0 finished tasks 3
storage-rack-01 running elapsed 161.0 projected 926.0 finished tasks 2
seq 17
=== server log tail (no errors expected) ===
INFO:     127.0.0.1:61675 - "GET /api/state?since=35 HTTP/1.1" 200 OK
INFO:     127.0.0.1:61676 - "GET / HTTP/1.1" 200 OK
INFO:     127.0.0.1:61679 - "POST /api/demo/run HTTP/1.1" 200 OK
INFO:     127.0.0.1:61680 - "GET /api/jobs HTTP/1.1" 200 OK
INFO:     127.0.0.1:61681 - "GET /api/state HTTP/1.1" 200 OK
0
```
</details>

---

## ‹Claude› · 23:15:13 UTC

**→ Bash**


*Stop server and verify clean shutdown*


```bash
pkill -f "uvicorn app.main:app" ; sleep 2; tail -4 /private/tmp/claude-501/-Users-arkadasgupta-work-critical-path-monitor/414c2a7c-83a4-4916-97b1-fc38c5fd5c81/scratchpad/server.log; echo "--- remaining procs ---"; pgrep -fl "uvicorn app.main" || echo "none"
```

---

## ‹User› · 23:15:19 UTC

<details><summary>result</summary>

```
INFO:     Shutting down
INFO:     Waiting for application shutdown.
INFO:     Application shutdown complete.
INFO:     Finished server process [92442]
--- remaining procs ---
none
```
</details>

---

## ‹Claude› · 23:15:37 UTC

Done. Created exactly two files; no existing file was modified.

## Files created

**`/Users/arkadasgupta/work/critical-path-monitor/app/demo.py`** — `async def seed(engine: Engine) -> list[JobSnapshot]` creating `compute-rack-01` (compute_rack) and `storage-rack-01` (storage_rack). Nothing pre-blocked.

**`/Users/arkadasgupta/work/critical-path-monitor/app/main.py`** — lifespan starts `Engine()` on `app.state.engine`, runner task on `app.state.runner`, seeds once at startup; every route reads `request.app.state.engine`. `/api/demo/run` cancels the runner (suppressing `CancelledError`), builds a fresh `Engine`, restarts the runner, then re-seeds. Shutdown cancels the runner the same way. Models: `CreateJobRequest`, `Edge`, `JobDetail(JobSnapshot)` (adds `edges`), `DemoRunResponse`, `StateResponse`. `StaticFiles` mounted at `/` last; `_ensure_static_dir()` creates `static/index.html` only if missing — the parallel author's real `index.html` was already present and was left untouched (`GET /` served their page, `<title>Critical Path Monitor</title>` + `/style.css`).

## Verification (live server, port 8000)

`GET /api/state` — config + jobs + events, two jobs auto-seeded and progressing:
```
config: {"max_jobs": 8, "max_concurrent_jobs": 2, "max_concurrent_tasks": 3, "tick_ms": 50, "reconcile_interval_ms": 250}
seq: 21
jobs: [('compute-rack-01','running',196.2,745.1,640), ('storage-rack-01','running',196.2,945.1,840)]
events: {'seq': 3, 'message': 'job compute-rack-01 admitted', ...} / 'site_survey started' ...
```
8 s later, same engine: `compute-rack-01 running elapsed 161.0 ... finished tasks 3` — advancing.

`GET /api/jobs/{id}` — snapshot fields plus `edges` (17 edges for compute_rack):
```
keys: [... 'tasks', 'computed_at_min', 'snapshot_seq', 'edges']
edges: [{"source":"site_survey","target":"rack_placement"},{"source":"rack_delivery","target":"rack_placement"}, ... {"source":"network_validation","target":"capacity_handoff"}]
GET /api/jobs/nope -> 404 {"detail":"unknown job nope"}
```

Block / unblock:
```
POST .../tasks/burn_in_test/block   -> 200  burn_in_test: blocked, capacity_handoff stalled: True
POST .../tasks/site_survey/block    -> 409  {"detail":"site_survey is running; only pending tasks can be blocked"}
POST .../tasks/burn_in_test/unblock -> 200  burn_in_test: pending
POST .../tasks/burn_in_test/unblock -> 409  {"detail":"burn_in_test is pending, not blocked"}
POST .../tasks/nope/block           -> 404  {"detail":"unknown job 4a55567b or task nope"}
```

Create job:
```
POST /api/jobs {"workflow":"storage_rack","name":"storage-rack-99"} -> 200
  {'job_id':'3d4f1666','workflow_name':'Storage rack buildout','state':'waiting','baseline_finish_min':840}
POST /api/jobs {"workflow":"nope"} -> 404 {"detail":"unknown workflow nope"}
9th job at MAX_JOBS=8      -> 429 {"detail":"at capacity: 8/8 jobs"}
```

`POST /api/demo/run` from that 8-job state:
```
{ok: True, jobs: [('compute-rack-01','waiting'), ('storage-rack-01','waiting')]}
GET /api/jobs -> job count after reset: 2   (fresh ids, event seq restarted at 7)
```
`GET /api/state?since=<current seq>` returned 0 events with the same `seq`. Server log had zero errors/tracebacks and shut down cleanly ("Application shutdown complete"); server killed, no processes left.
