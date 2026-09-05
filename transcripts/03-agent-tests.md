# Subagent — test suite

Verbatim record of the Claude Code session that built this project, converted from the raw JSONL log. Model reasoning ("thinking") blocks are omitted; very long tool outputs are truncated with the original length noted.

---


---

## ‹User› · 23:11:21 UTC

Write tests for an existing Python project at /Users/arkadasgupta/work/critical-path-monitor.

Create ONLY: tests/__init__.py, tests/test_critical_path.py, tests/test_engine.py. DO NOT modify any file under app/ — if you believe you have found a bug in app/, report it, do not fix it.

Run with: `cd /Users/arkadasgupta/work/critical-path-monitor && PYTHONPATH=. uv run pytest -q`
pytest is already a dev dependency. `uv add --dev pytest-asyncio` if you need it (you will, for the engine tests).

## What the code does

A workflow engine for datacenter rack buildouts. Tasks form a DAG; each has a duration in simulated minutes. It runs critical-path analysis (CPM) so that blocking a task reports what that block COSTS in delivery time.

Read these first: app/models.py, app/critical_path_evaluator.py, app/scheduler.py, app/workflows.py, app/config.py.

Key API:
- `app.workflows.WORKFLOWS` — dict with `compute_rack` and `storage_rack`.
- `WorkflowDef.static[task_id]` -> StaticSchedule(es, ef, ls, lf, buffer_min, on_critical_path)
- `WorkflowDef.baseline_finish_min`
- `app.critical_path_evaluator.compute_snapshot(job, now_min, snapshot_seq) -> JobSnapshot`
- `app.scheduler.Engine` — `await engine.run()` (long-running, supervise as a task), `create_job(workflow_id, name)`, `block_task(job_id, task_id)`, `unblock_task(job_id, task_id)`, `.jobs`, `.snapshots`, `.events`. Raises `JobLimitReached`, `TaskNotBlockable`, `TaskNotBlocked`.

Config is read from env at import: MAX_JOBS, MAX_CONCURRENT_JOBS, MAX_CONCURRENT_TASKS, TICK_MS, RECONCILE_INTERVAL_MS. To test with different values, set `os.environ` BEFORE importing app modules, or use `importlib.reload`. Note `app.scheduler` reads `config.MAX_*` at Engine construction, so monkeypatching `app.config.MAX_JOBS` before constructing an Engine also works — verify which approach actually works and use it.

## tests/test_critical_path.py — pure, fast, no event loop

These values are already verified correct; assert them exactly as regression anchors:

compute_rack: baseline_finish_min == 640. Buffers: site_survey 0, rack_delivery 15, rack_placement 15, power_whip_install 0, network_fabric_pull 140, pdu_install 0, tor_switch_install 140, gpu_node_install 0, node_cabling 0, firmware_flash 0, burn_in_test 0, network_validation 165, capacity_handoff 0.
storage_rack: baseline_finish_min == 840. Buffers: network_fabric_pull 310, tor_switch_install 310, network_validation 195, rack_delivery 15, rack_placement 15; the chain site_survey, power_whip_install, pdu_install, jbod_shelf_install, disk_population, node_cabling, firmware_flash, array_init, capacity_handoff all 0.

Also test:
- `on_critical_path` is exactly `buffer_min == 0` for every task in both workflows.
- The critical path is a connected chain from a root to a sink.
- Every task: `ef == es + duration_min` and `ls == lf - duration_min`.
- A brand-new job (never started) snapshots to `projected_finish_min == baseline_finish_min` and `delay_incurred_min == 0` and state `waiting`.
- Construct a Job by hand with some tasks marked FINISHED at late times and assert `delay_incurred_min` moves by the right amount.
- A cyclic DAG raises on construction (use `app.critical_path_evaluator.build_workflow` with a hand-made cyclic tuple of TaskDef).

## tests/test_engine.py — async, end-to-end against Engine directly (no HTTP)

Use a small TICK_MS (e.g. 5) so tests are fast, and generous MAX_CONCURRENT_TASKS unless the test is about contention. Always cancel the `engine.run()` task in a finally block. Poll with a bounded timeout helper rather than sleeping a fixed amount and hoping — job state is eventually consistent (reconciler tick), so `assert` immediately after an action will be flaky.

Cover:
1. A job runs to completion; final state `finished`; `delay_incurred_min` is small (allow generous slack for loop overhead, e.g. < 40 sim-minutes).
2. Blocking `network_fabric_pull` (buffer 140) and holding it well under 140 sim-minutes leaves `delay_incurred_min` near 0, while its `buffer_remaining_min` visibly decreases.
3. Blocking `power_whip_install` (buffer 0) and holding it causes `delay_incurred_min` to grow roughly in step with the hold, and its `buffer_remaining_min` goes negative.
4. Blocking a RUNNING task raises `TaskNotBlockable`; blocking a FINISHED task raises `TaskNotBlockable`; unblocking a task that is not blocked raises `TaskNotBlocked`.
5. Blocking every startable task drives job state to `blocked` within a bounded wait, and the job releases its concurrency slot (inspect `engine._admitted`) so another queued job can be admitted. Then unblocking re-admits it and it reaches `finished`.
6. `MAX_CONCURRENT_TASKS = 1` serializes: sample snapshots repeatedly and assert the count of `running` tasks across ALL jobs never exceeds 1.
7. `MAX_JOBS` exceeded raises `JobLimitReached`.
8. Unknown workflow id raises KeyError; unknown job/task id raises KeyError.

Make the tests deterministic and not timing-fragile: prefer polling-until-condition with a timeout over fixed sleeps, and use tolerant numeric bounds where scheduling overhead is involved.

Report: the full `pytest -q` output, and any test you could not make pass along with your diagnosis of whether it is a test problem or a real bug in app/.

---

## ‹Claude› · 23:11:22 UTC

I'll start by reading the app code.

---

## ‹Claude› · 23:11:24 UTC

**→ Bash**


*List project files*


```bash
find /Users/arkadasgupta/work/critical-path-monitor -type f -name "*.py" -o -name "*.toml" -o -name "*.cfg" | grep -v ".venv" | head -50
```

---

## ‹User› · 23:11:24 UTC

<details><summary>result</summary>

```
/Users/arkadasgupta/work/critical-path-monitor/pyproject.toml
/Users/arkadasgupta/work/critical-path-monitor/app/config.py
/Users/arkadasgupta/work/critical-path-monitor/app/models.py
/Users/arkadasgupta/work/critical-path-monitor/app/events.py
/Users/arkadasgupta/work/critical-path-monitor/app/__init__.py
/Users/arkadasgupta/work/critical-path-monitor/app/critical_path_evaluator.py
/Users/arkadasgupta/work/critical-path-monitor/app/workflows.py
/Users/arkadasgupta/work/critical-path-monitor/app/scheduler.py
/Users/arkadasgupta/work/critical-path-monitor/app/demo.py
```
</details>

---

## ‹Claude› · 23:11:24 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/app/models.py`


---

## ‹User› · 23:11:24 UTC

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

## ‹Claude› · 23:11:26 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/app/critical_path_evaluator.py`


---

## ‹User› · 23:11:26 UTC

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

## ‹Claude› · 23:11:26 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/app/scheduler.py`


---

## ‹User› · 23:11:26 UTC

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

## ‹Claude› · 23:11:29 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/app/workflows.py`


---

## ‹Claude› · 23:11:29 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/app/config.py`


---

## ‹User› · 23:11:29 UTC

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

## ‹User› · 23:11:29 UTC

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

## ‹Claude› · 23:11:29 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/pyproject.toml`


---

## ‹User› · 23:11:29 UTC

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

## ‹Claude› · 23:11:30 UTC

**→ Read**


`/Users/arkadasgupta/work/critical-path-monitor/app/events.py`


---

## ‹User› · 23:11:30 UTC

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

## ‹Claude› · 23:12:58 UTC

Now let me verify the numbers and environment.

---

## ‹Claude› · 23:12:59 UTC

**→ Bash**


*Check env and demo*


```bash
ls -a && cat app/demo.py | head -40 && uv run python -c "import pytest, sys; print(pytest.__version__, sys.version)"
```

---

## ‹User› · 23:13:03 UTC

<details><summary>result</summary>

```
.
..
.claude
.venv
app
pyproject.toml
static
uv.lock
"""Demo seeding.

One compute buildout and one storage buildout, which is exactly enough to make
the shared config knobs visible: MAX_CONCURRENT_JOBS admits both, and the two
workflows then contend for the same MAX_CONCURRENT_TASKS worker pool.

Nothing is pre-blocked on purpose. Blocking is the one interaction worth
performing by hand, and a seeded block would spend it before anyone looks.
"""

from app.models import JobSnapshot
from app.scheduler import Engine

SEEDS: tuple[tuple[str, str], ...] = (
    ("compute_rack", "compute-rack-01"),
    ("storage_rack", "storage-rack-01"),
)


async def seed(engine: Engine) -> list[JobSnapshot]:
    """Create the demo jobs on a fresh engine and return their snapshots."""
    return [
        await engine.create_job(workflow_id, name) for workflow_id, name in SEEDS
    ]
9.1.1 3.12.13 (main, Aug  7 2026, 02:15:23) [Clang 22.1.3 ]
```
</details>

---

## ‹Claude› · 23:13:12 UTC

**→ Bash**


*Verify CPM numbers*


```bash
PYTHONPATH=. uv run python - <<'EOF'
from app.workflows import WORKFLOWS
from app.models import Job, Task, TaskState
from app.critical_path_evaluator import compute_snapshot, build_workflow
from app.models import TaskDef

for wid, wf in WORKFLOWS.items():
    print(wid, "baseline", wf.baseline_finish_min)
    for t in wf.task_defs:
        s = wf.static[t.id]
        print("  ", t.id, s.es, s.ef, s.ls, s.lf, s.buffer_min, s.on_critical_path)

wf = WORKFLOWS["compute_rack"]
def mk(fin):
    tasks = {}
    for t in wf.task_defs:
        if t.id in fin:
            st, ft = fin[t.id]
            tasks[t.id] = Task(id=t.id, state=TaskState.FINISHED, started_at_min=st, finished_at_min=ft)
        else:
            tasks[t.id] = Task(id=t.id)
    return Job(id="j", name="j", workflow=wf, tasks=tasks, created_at_min=0.0, started_at_min=0.0)

j = mk({"site_survey":(0,30),"rack_delivery":(0,60),"rack_placement":(60,105),"power_whip_install":(30,150)})
s = compute_snapshot(j, 150.0, 1)
print("scenario delay", s.delay_incurred_min, "proj", s.projected_finish_min, "state", s.state, "elapsed", s.elapsed_min)

# brand new job never started
tasks = {t.id: Task(id=t.id) for t in wf.task_defs}
j2 = Job(id="j2", name="j2", workflow=wf, tasks=tasks, created_at_min=0.0)
s2 = compute_snapshot(j2, 0.0, 1)
print("new job", s2.state, s2.projected_finish_min, s2.delay_incurred_min)

# cyclic
try:
    build_workflow("c","c",(TaskDef(id="a",label="a",duration_min=1,parent_task_ids=("b",)),TaskDef(id="b",label="b",duration_min=1,parent_task_ids=("a",))))
except Exception as e:
    print("cycle ->", type(e).__name__, e)
EOF
```

---

## ‹User› · 23:13:22 UTC

<details><summary>result</summary>

```
compute_rack baseline 640
   site_survey 0 30 0 30 0 True
   rack_delivery 0 60 15 75 15 False
   rack_placement 60 105 75 120 15 False
   power_whip_install 30 120 30 120 0 True
   network_fabric_pull 30 150 170 290 140 False
   pdu_install 120 160 120 160 0 True
   tor_switch_install 150 200 290 340 140 False
   gpu_node_install 160 340 160 340 0 True
   node_cabling 340 410 340 410 0 True
   firmware_flash 410 470 410 470 0 True
   burn_in_test 470 620 470 620 0 True
   network_validation 410 455 575 620 165 False
   capacity_handoff 620 640 620 640 0 True
storage_rack baseline 840
   site_survey 0 30 0 30 0 True
   rack_delivery 0 60 15 75 15 False
   rack_placement 60 105 75 120 15 False
   power_whip_install 30 120 30 120 0 True
   network_fabric_pull 30 150 340 460 310 False
   pdu_install 120 160 120 160 0 True
   tor_switch_install 150 200 460 510 310 False
   jbod_shelf_install 160 310 160 310 0 True
   disk_population 310 510 310 510 0 True
   node_cabling 510 580 510 580 0 True
   firmware_flash 580 640 580 640 0 True
   array_init 640 820 640 820 0 True
   network_validation 580 625 775 820 195 False
   capacity_handoff 820 840 820 840 0 True
scenario delay 30.0 proj 670.0 state JobState.RUNNING elapsed 150.0
new job JobState.WAITING 640.0 0.0
cycle -> ValidationError 1 validation error for WorkflowDef
  Value error, workflow graph has a cycle involving ['a', 'b'] [type=value_error, input_value={'id': 'c', 'name': 'c', ...arent_task_ids=('a',)))}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/value_error
```
</details>

---

## ‹Claude› · 23:14:26 UTC

All buffer values match the spec. Let me add pytest-asyncio and write the tests.

---

## ‹Claude› · 23:14:27 UTC

**→ Bash**


*Add pytest-asyncio dev dep*


```bash
uv add --dev pytest-asyncio 2>&1 | tail -5
```

---

## ‹User› · 23:14:32 UTC

<details><summary>result</summary>

```
Prepared 2 packages in 205ms
Uninstalled 1 package in 0.99ms
Installed 2 packages in 1ms
 ~ critical-path-monitor==0.1.0 (from file:///Users/arkadasgupta/work/critical-path-monitor)
 + pytest-asyncio==1.4.0
```
</details>

---

## ‹Claude› · 23:14:34 UTC

**→ Write**


`/Users/arkadasgupta/work/critical-path-monitor/tests/__init__.py`


---

## ‹User› · 23:14:40 UTC

<details><summary>result</summary>

```
File created successfully at: /Users/arkadasgupta/work/critical-path-monitor/tests/__init__.py (file state is current in your context — no need to Read it back)
```
</details>

---

## ‹Claude› · 23:15:39 UTC

**→ Write**


`/Users/arkadasgupta/work/critical-path-monitor/tests/test_critical_path.py`


```
"""Pure CPM tests: no event loop, no scheduler, no sleeping.

Everything here is a function of the DAG plus stored task facts, so the numbers
are exact and can be asserted as regression anchors.
"""

import pytest

from app.critical_path_evaluator import build_workflow, compute_snapshot
from app.models import Job, JobState, Task, TaskDef, TaskState
from app.workflows import WORKFLOWS

COMPUTE_RACK_BUFFERS = {
    "site_survey": 0,
    "rack_delivery": 15,
    "rack_placement": 15,
    "power_whip_install": 0,
    "network_fabric_pull": 140,
    "pdu_install": 0,
    "tor_switch_install": 140,
    "gpu_node_install": 0,
    "node_cabling": 0,
    "firmware_flash": 0,
    "burn_in_test": 0,
    "network_validation": 165,
    "capacity_handoff": 0,
}

STORAGE_RACK_BUFFERS = {
    "site_survey": 0,
    "rack_delivery": 15,
    "rack_placement": 15,
    "power_whip_install": 0,
    "network_fabric_pull": 310,
    "pdu_install": 0,
    "tor_switch_install": 310,
    "jbod_shelf_install": 0,
    "disk_population": 0,
    "node_cabling": 0,
    "firmware_flash": 0,
    "array_init": 0,
    "network_validation": 195,
    "capacity_handoff": 0,
}

EXPECTED = {
    "compute_rack": (640, COMPUTE_RACK_BUFFERS),
    "storage_rack": (840, STORAGE_RACK_BUFFERS),
}


def _new_job(workflow_id: str, *, started: bool = False) -> Job:
    workflow = WORKFLOWS[workflow_id]
    return Job(
        id="job-1",
        name="job-1",
        workflow=workflow,
        tasks={t.id: Task(id=t.id) for t in workflow.task_defs},
        created_at_min=0.0,
        started_at_min=0.0 if started else None,
    )


def _finish(job: Job, task_id: str, started_at: float, finished_at: float) -> None:
    job.tasks[task_id] = Task(
        id=task_id,
        state=TaskState.FINISHED,
        started_at_min
… truncated
```

---

## ‹User› · 23:15:44 UTC

<details><summary>result</summary>

```
File created successfully at: /Users/arkadasgupta/work/critical-path-monitor/tests/test_critical_path.py (file state is current in your context — no need to Read it back)
```
</details>

---

## ‹Claude› · 23:15:46 UTC

**→ Bash**


*Run CPM tests*


```bash
PYTHONPATH=. uv run pytest -q tests/test_critical_path.py 2>&1 | tail -40
```

---

## ‹User› · 23:15:48 UTC

<details><summary>result</summary>

```
_____________ test_late_slack_task_within_its_buffer_costs_nothing _____________

    def test_late_slack_task_within_its_buffer_costs_nothing() -> None:
        """network_fabric_pull carries 140 minutes of buffer; 100 late is free."""
        job = _new_job("compute_rack", started=True)
        _finish(job, "site_survey", 0, 30)
        _finish(job, "rack_delivery", 0, 60)
        _finish(job, "rack_placement", 60, 105)
        _finish(job, "power_whip_install", 30, 120)
        _finish(job, "network_fabric_pull", 30, 250)  # planned EF 150, so 100 late
    
        snapshot = compute_snapshot(job, now_min=250.0, snapshot_seq=4)
    
>       assert snapshot.delay_incurred_min == 0
E       AssertionError: assert 130.0 == 0
E        +  where 130.0 = JobSnapshot(job_id='job-1', name='job-1', workflow_id='compute_rack', workflow_name='Compute rack buildout', state=<Jo...min=0, on_critical_path=True, es=750.0, ef=770.0, buffer_remaining_min=-130.0)), computed_at_min=250.0, snapshot_seq=4).delay_incurred_min

tests/test_critical_path.py:262: AssertionError
____________ test_slack_task_late_beyond_its_buffer_starts_costing _____________

    def test_slack_task_late_beyond_its_buffer_starts_costing() -> None:
        """40 minutes past the 140-minute buffer costs exactly 40."""
        job = _new_job("compute_rack", started=True)
        _finish(job, "site_survey", 0, 30)
        _finish(job, "rack_delivery", 0, 60)
        _finish(job, "rack_placement", 60, 105)
        _finish(job, "power_whip_install", 30, 120)
        _finish(job, "network_fabric_pull", 30, 330)  # planned EF 150, so 180 late
    
        snapshot = compute_snapshot(job, now_min=330.0, snapshot_seq=5)
    
>       assert snapshot.delay_incurred_min == 40
E       AssertionError: assert 210.0 == 40
E        +  where 210.0 = JobSnapshot(job_id='job-1', name='job-1', workflow_id='compute_rack', workflow_name='Compute rack buildout', state=<Jo...min=0, on_critical_path=True, es=830.0, ef=850.0, buffer_remaining_min=-210.0)), computed_at_min=330.0, snapshot_seq=5).delay_incurred_min

tests/test_critical_path.py:283: AssertionError
=========================== short test summary info ============================
FAILED tests/test_critical_path.py::test_late_slack_task_within_its_buffer_costs_nothing
FAILED tests/test_critical_path.py::test_slack_task_late_beyond_its_buffer_starts_costing
2 failed, 35 passed in 0.07s
```
</details>

---

## ‹Claude› · 23:16:01 UTC

The elapsed-floor on pending tasks makes those two scenarios self-contradictory. Let me make them consistent.

---

## ‹Claude› · 23:16:14 UTC

**→ Edit**


`/Users/arkadasgupta/work/critical-path-monitor/tests/test_critical_path.py`


```
def _job_with_late_fabric_pull(fabric_finished_at: float) -> Job:
    """Critical chain exactly on plan; only network_fabric_pull runs late.

    gpu_node_install is left RUNNING from its planned start of 160 so the
    critical chain stays consistent with the elapsed clock - every PENDING
    task's ES is floored at `elapsed`, so a task that "should" already be
    done but is left pending would itself register as a slip.
    """
    job = _new_job("compute_rack", started=True)
    _finish(job, "site_survey", 0, 30)
    _finish(job, "rack_delivery", 0, 60)
    _finish(job, "rack_placement", 60, 105)
    _finish(job, "power_whip_install", 30, 120)
    _finish(job, "pdu_install", 120, 160)
    _finish(job, "network_fabric_pull", 30, fabric_finished_at)
    job.tasks["gpu_node_install"] = Task(
        id="gpu_node_install", state=TaskState.RUNNING, started_at_min=160
    )
    return job


def test_late_slack_task_within_its_buffer_costs_nothing() -> None:
    """network_fabric_pull carries 140 minutes of buffer; 100 late is free."""
    job = _job_with_late_fabric_pull(250)  # planned EF 150, so 100 late

    snapshot = compute_snapshot(job, now_min=250.0, snapshot_seq=4)

    assert snapshot.projected_finish_min == 640
    assert snapshot.delay_incurred_min == 0

    fabric = next(t for t in snapshot.tasks if t.id == "network_fabric_pull")
    assert not fabric.on_critical_path
    assert fabric.buffer_min == 140
    # Started on time, so the start-edge buffer is untouched by a late finish.
    assert fabric.buffer_remaining_min == 140


def test_slack_task_late_beyond_its_buffer_starts_costing() -> None:
    """40 minutes past the 140-minute buffer costs exactly 40."""
    job = _job_with_late_fabric_pull(330)  # planned EF 150, so 180 late

    snapshot = compute_sna
… truncated
```

---

## ‹User› · 23:16:19 UTC

<details><summary>result</summary>

```
The file /Users/arkadasgupta/work/critical-path-monitor/tests/test_critical_path.py has been updated successfully. (file state is current in your context — no need to Read it back)
```
</details>

---

## ‹Claude› · 23:16:20 UTC

**→ Bash**


*Rerun CPM tests*


```bash
PYTHONPATH=. uv run pytest -q tests/test_critical_path.py 2>&1 | tail -30
```

---

## ‹User› · 23:16:23 UTC

<details><summary>result</summary>

```
.....................................                                    [100%]
37 passed in 0.06s
```
</details>

---

## ‹Claude› · 23:17:29 UTC

**→ Write**


`/Users/arkadasgupta/work/critical-path-monitor/tests/test_engine.py`


```
"""End-to-end tests against Engine directly (no HTTP layer).

Timing discipline used throughout:

* TICK_MS is squeezed to 5 real milliseconds per simulated minute, so a
  640-minute compute buildout runs in a bit over three real seconds.
* Job state is eventually consistent (the reconciler tick), so every assertion
  about derived state goes through `wait_until`, a bounded poll, rather than a
  fixed sleep followed by a hopeful assert.
* Numeric assertions carry slack for event-loop overhead: a simulated minute is
  5ms, so ordinary scheduling jitter is worth a couple of simulated minutes.

`app.scheduler` reads `config.MAX_*` when the Engine is constructed
(MAX_CONCURRENT_JOBS) or when `run()` starts (MAX_CONCURRENT_TASKS), and
`config.MAX_JOBS` / `config.TICK_MS` on every use - so monkeypatching
`app.config` before building the Engine is enough, and no module reload is
needed. `running_engine` patches first, constructs second.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from typing import TypeVar

import pytest

from app import config
from app.models import JobSnapshot, JobState, TaskState
from app.scheduler import (
    Engine,
    JobLimitReached,
    TaskNotBlockable,
    TaskNotBlocked,
)

pytestmark = pytest.mark.asyncio

T = TypeVar("T")

# Real milliseconds per simulated minute for these tests.
TICK_MS = 5
# compute_rack baseline is 640 simulated minutes -> ~3.2s of wall clock.
COMPUTE_RACK_SECONDS = 640 * TICK_MS / 1000.0

POLL_INTERVAL = 0.005


@contextlib.asynccontextmanager
async def running_engine(monkeypatch: pytest.MonkeyPatch, **overrides: int) -> AsyncIterator[Engine]:
    """A supervised Engine with test config, torn down on the way out."""
    settings: dict[str, int] = {
        "TICK_MS": TICK_MS,
… truncated
```

---

## ‹User› · 23:17:35 UTC

<details><summary>result</summary>

```
File created successfully at: /Users/arkadasgupta/work/critical-path-monitor/tests/test_engine.py (file state is current in your context — no need to Read it back)
```
</details>

---

## ‹Claude› · 23:17:37 UTC

**→ Bash**


*Run full test suite*


```bash
PYTHONPATH=. timeout 300 uv run pytest -q 2>&1 | tail -60
```

---

## ‹User› · 23:17:39 UTC

<details><summary>result</summary>

```
(eval):1: command not found: timeout
```
</details>

---

## ‹Claude› · 23:17:41 UTC

**→ Bash**


*Run full test suite*


```bash
PYTHONPATH=. uv run pytest -q 2>&1 | tail -60
```

---

## ‹User› · 23:17:56 UTC

<details><summary>result</summary>

```
...................................................                      [100%]
51 passed in 13.36s
```
</details>

---

## ‹Claude› · 23:17:58 UTC

All 51 pass. Let me check for flakiness with repeated runs.

---

## ‹Claude› · 23:17:59 UTC

**→ Bash**


*Repeat suite three times*


```bash
for i in 1 2 3; do PYTHONPATH=. uv run pytest -q 2>&1 | tail -3; done
```

---

## ‹User› · 23:18:42 UTC

<details><summary>result</summary>

```
...................................................                      [100%]
51 passed in 13.36s
...................................................                      [100%]
51 passed in 13.35s
...................................................                      [100%]
51 passed in 13.34s
```
</details>

---

## ‹Claude› · 23:18:44 UTC

**→ Bash**


*Run engine tests concurrently to stress timing*


```bash
PYTHONPATH=. uv run pytest -q -p no:randomly 2>&1 | tail -3; echo "--- under load ---"; for i in 1 2 3 4; do (PYTHONPATH=. uv run pytest -q tests/test_engine.py 2>&1 | tail -2) & done; wait
```

---

## ‹User› · 23:19:13 UTC

<details><summary>result</summary>

```
...................................................                      [100%]
51 passed in 13.35s
--- under load ---
..............                                                           [100%]
14 passed in 13.35s
..............                                                           [100%]
14 passed in 13.35s
..............                                                           [100%]
14 passed in 13.35s
..............                                                           [100%]
14 passed in 13.35s
```
</details>

---

## ‹Claude› · 23:19:19 UTC

**→ Bash**


*Check app untouched*


```bash
ls -lT app/ | head -20 && PYTHONPATH=. uv run pytest -q tests/test_engine.py::test_job_runs_to_completion -s 2>&1 | tail -3
```

---

## ‹User› · 23:19:24 UTC

<details><summary>result</summary>

```
total 112
-rw-r--r--@  1 arkadasgupta  staff      0 Sep  4 15:51:48 2026 __init__.py
drwxr-xr-x@ 11 arkadasgupta  staff    352 Sep  4 16:13:51 2026 __pycache__
-rw-r--r--@  1 arkadasgupta  staff   1804 Sep  4 15:30:39 2026 config.py
-rw-r--r--@  1 arkadasgupta  staff   7964 Sep  4 15:51:41 2026 critical_path_evaluator.py
-rw-r--r--@  1 arkadasgupta  staff    824 Sep  4 16:11:38 2026 demo.py
-rw-r--r--@  1 arkadasgupta  staff    968 Sep  4 15:56:15 2026 events.py
-rw-r--r--@  1 arkadasgupta  staff   6663 Sep  4 16:13:39 2026 main.py
-rw-r--r--@  1 arkadasgupta  staff   5279 Sep  4 15:46:50 2026 models.py
-rw-r--r--@  1 arkadasgupta  staff  10268 Sep  4 16:08:46 2026 scheduler.py
-rw-r--r--@  1 arkadasgupta  staff   4173 Sep  4 15:52:14 2026 workflows.py
.
1 passed in 3.25s
```
</details>

---

## ‹Claude› · 23:19:33 UTC

**→ Bash**


*Measure delay margin*


```bash
cat > /private/tmp/claude-501/-Users-arkadasgupta-work-critical-path-monitor/414c2a7c-83a4-4916-97b1-fc38c5fd5c81/scratchpad/margin.py <<'EOF'
import asyncio, contextlib
from app import config
from app.models import JobState
config.TICK_MS=5; config.RECONCILE_INTERVAL_MS=20; config.MAX_CONCURRENT_TASKS=16
from app.scheduler import Engine

async def main():
    e = Engine()
    t = asyncio.create_task(e.run())
    j = (await e.create_job("compute_rack","m")).job_id
    while e.snapshots[j].state is not JobState.FINISHED:
        await asyncio.sleep(0.005)
    print("delay", e.snapshots[j].delay_incurred_min, "elapsed", e.snapshots[j].elapsed_min)
    t.cancel()
    with contextlib.suppress(asyncio.CancelledError): await t
asyncio.run(main())
EOF
PYTHONPATH=. uv run python /private/tmp/claude-501/-Users-arkadasgupta-work-critical-path-monitor/414c2a7c-83a4-4916-97b1-fc38c5fd5c81/scratchpad/margin.py
```

---

## ‹User› · 23:19:38 UTC

<details><summary>result</summary>

```
delay 0.9161165938712656 elapsed 640.9235166036524
```
</details>

---

## ‹Claude› · 23:19:55 UTC

**→ Bash**


*Final full run*


```bash
rm -f /private/tmp/claude-501/-Users-arkadasgupta-work-critical-path-monitor/414c2a7c-83a4-4916-97b1-fc38c5fd5c81/scratchpad/margin.py; PYTHONPATH=. uv run pytest -q 2>&1
```

---

## ‹User› · 23:20:11 UTC

<details><summary>result</summary>

```
...................................................                      [100%]
51 passed in 13.35s
```
</details>

---

## ‹Claude› · 23:20:33 UTC

All 51 tests pass, and they're stable — I ran the suite four consecutive times plus four copies of the engine tests concurrently (to induce CPU contention) with zero failures.

## Full `pytest -q` output

```
...................................................                      [100%]
51 passed in 13.35s
```

## What was created

- `/Users/arkadasgupta/work/critical-path-monitor/tests/__init__.py`
- `/Users/arkadasgupta/work/critical-path-monitor/tests/test_critical_path.py` — 37 tests, 0.06s, no event loop
- `/Users/arkadasgupta/work/critical-path-monitor/tests/test_engine.py` — 14 tests, ~13s

Nothing under `app/` was touched (mtimes all predate this session). `uv add --dev pytest-asyncio` added `pytest-asyncio==1.4.0` to `pyproject.toml`/`uv.lock`; I used `pytestmark = pytest.mark.asyncio` so no pytest config change was needed.

## Findings

**No bugs found in `app/`.** Every test in the brief passes as specified, plus extras (event-log lifecycle, a `MAX_CONCURRENT_TASKS=8` counterpart proving the cap is what serializes, declaration-order of task views, `stalled` propagation, duplicate/dangling/self-loop graph validation, a hand-built diamond exercising `max()` vs `sum()` in the forward pass).

**Config approach — verified.** Monkeypatching `app.config.*` before constructing the `Engine` is sufficient for all five knobs; no `importlib.reload` or pre-import `os.environ` is needed. `MAX_CONCURRENT_JOBS` is read in `Engine.__init__` (semaphore size), `MAX_CONCURRENT_TASKS` in `run()` (worker count), and `MAX_JOBS`/`TICK_MS` on every use. My `running_engine` async context manager patches first, constructs second, and always cancels the supervisor task in a `finally`.

**Two things worth knowing about the model, neither a bug:**

1. `compute_snapshot` floors every PENDING/BLOCKED task's ES at `elapsed` ("nothing can start in the past"). This makes hand-built `Job` fixtures easy to get wrong: my first attempt at "slack task finished 100 min late costs nothing" asserted 0 but got 130, because I left `pdu_install` PENDING at `elapsed=250` when the plan has it finishing at 160 — the fixture was itself describing a slipped critical path. The fixed helper `_job_with_late_fabric_pull` keeps the critical chain consistent with the clock (leaving `gpu_node_install` RUNNING from its planned start of 160), and then the exact numbers fall out: 100 late inside a 140 buffer costs 0; 180 late costs exactly 40.

2. A corollary in the live engine: if `MAX_CONCURRENT_TASKS` is saturated, tasks that are queued-but-not-yet-running are PENDING and get floored at `elapsed`, so `delay_incurred_min` reflects worker-pool contention as delivery delay. That is arguably correct behavior (the work really is late), but it means the "small delay" assertion in the completion test only holds with a generous task pool. I used `MAX_CONCURRENT_TASKS=16` there.

**Timing headroom is large, not marginal.** A clean `compute_rack` run at `TICK_MS=5` finishes with `delay_incurred_min ≈ 0.92` against the test's `< 40` bound, and `elapsed ≈ 640.9` against a 640 baseline. All engine assertions go through a bounded `wait_until` poll (5ms interval) rather than fixed sleeps, and numeric comparisons use `pytest.approx(..., abs=10)` on quantities measured in simulated minutes worth 5ms each.

**One implementation detail the tests lean on deliberately:** `create_job` and `block_task` are `async def` but contain no `await`, so `await engine.create_job(...)` followed by `await engine.block_task(...)` cannot be interleaved by the dispatcher. That is what makes it reliable to block a root task (`site_survey`, `rack_delivery`) before a worker can pick it up — which tests 2, 3, and 5 all require. It is noted in a comment in the slack-block test. If someone later adds an `await` to either method, those three tests would start failing intermittently with `TaskNotBlockable`, which is a fair early warning: the scheduler's module docstring makes the same no-await-mid-mutation promise.
