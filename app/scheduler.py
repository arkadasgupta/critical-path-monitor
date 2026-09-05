"""In-memory job and task scheduler.

Task concurrency is capped by the size of the worker pool draining a single
global task queue. The queue is global rather than per-job so that concurrent
jobs contend with each other, which is what makes MAX_CONCURRENT_TASKS
observable.

There are deliberately no locks. Every section that reads and then mutates
shared state contains no await, so under cooperative scheduling it cannot
interleave. Queue puts always happen after the mutation, never inside it. If you
add an await mid-mutation, that invariant breaks and you will need a lock - so
don't, or say why in a comment.

The one thing that genuinely changes underneath us is a task being blocked while
it sits in the task queue. Workers re-check task state on dequeue rather than
trusting that a queued task is still runnable.
"""

import asyncio
import time
import uuid
from collections import deque

from app import config
from app.critical_path_evaluator import compute_snapshot
from app.events import EventLog
from app.models import Job, JobSnapshot, JobState, Task, TaskState
from app.workflows import WORKFLOWS

_ORIGIN = time.monotonic()


def now_min() -> float:
    return (time.monotonic() - _ORIGIN) * 1000.0 / config.TICK_MS


def _minutes_to_seconds(minutes: float) -> float:
    return minutes * config.TICK_MS / 1000.0


class JobLimitReached(Exception):
    pass


class TaskNotBlockable(Exception):
    pass


class TaskNotBlocked(Exception):
    pass


class Engine:
    def __init__(self) -> None:
        self.jobs: dict[str, Job] = {}
        self.snapshots: dict[str, JobSnapshot] = {}
        self.events = EventLog()

        # A deque rather than an asyncio.Queue because an unblocked job goes to
        # the front: it already waited once and should not queue again behind
        # jobs that were admitted while it was stuck.
        self._job_queue: deque[str] = deque()
        self._job_available = asyncio.Event()

        self._task_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._enqueued: set[tuple[str, str]] = set()
        self._job_slots = asyncio.Semaphore(config.MAX_CONCURRENT_JOBS)
        self._admitted: set[str] = set()
        self._snapshot_seq = 0

    async def run(self) -> None:
        async with asyncio.TaskGroup() as group:
            group.create_task(self._dispatcher())
            group.create_task(self._reconciler())
            for _ in range(config.MAX_CONCURRENT_TASKS):
                group.create_task(self._worker())

    async def create_job(self, workflow_id: str, name: str | None = None) -> JobSnapshot:
        workflow = WORKFLOWS[workflow_id]
        if len(self.jobs) >= config.MAX_JOBS:
            raise JobLimitReached(f"at capacity: {len(self.jobs)}/{config.MAX_JOBS} jobs")
        job_id = uuid.uuid4().hex[:8]
        job = Job(
            id=job_id,
            name=name or f"{workflow_id}-{job_id}",
            workflow=workflow,
            tasks={t.id: Task(id=t.id) for t in workflow.task_defs},
            created_at_min=now_min(),
        )
        self.jobs[job_id] = job
        self.events.emit(now_min(), f"job {job.name} queued", job_id)
        self._snapshot(job)
        self._push_job(job_id)
        return self.snapshots[job_id]

    async def block_task(self, job_id: str, task_id: str) -> JobSnapshot:
        job = self.jobs[job_id]
        task = job.tasks[task_id]
        if task.state is not TaskState.PENDING:
            raise TaskNotBlockable(
                f"{task_id} is {task.state.value}; only pending tasks can be blocked"
            )
        task.state = TaskState.BLOCKED
        self.events.emit(now_min(), f"{task_id} blocked", job_id)
        # Reconcile this job now rather than waiting for the tick, so the one
        # interaction a user actually performs feels instant.
        self._snapshot(job)
        self._release_slot_if_blocked(job)
        return self.snapshots[job_id]

    async def unblock_task(self, job_id: str, task_id: str) -> JobSnapshot:
        job = self.jobs[job_id]
        task = job.tasks[task_id]
        if task.state is not TaskState.BLOCKED:
            raise TaskNotBlocked(f"{task_id} is {task.state.value}, not blocked")
        task.state = TaskState.PENDING
        self.events.emit(now_min(), f"{task_id} unblocked", job_id)

        ready: list[tuple[str, str]] = []
        if job_id in self._admitted:
            ready = self._collect_ready(job, (task_id,))
        elif job.started_at_min is not None and self._is_startable(job, task_id):
            self._push_job(job_id, front=True)
            self.events.emit(now_min(), f"job {job.name} requeued", job_id)
        self._snapshot(job)

        for item in ready:
            await self._task_queue.put(item)
        return self.snapshots[job_id]

    def _snapshot(self, job: Job) -> None:
        self._snapshot_seq += 1
        self.snapshots[job.id] = compute_snapshot(job, now_min(), self._snapshot_seq)

    def _push_job(self, job_id: str, front: bool = False) -> None:
        if front:
            self._job_queue.appendleft(job_id)
        else:
            self._job_queue.append(job_id)
        self._job_available.set()

    async def _next_job(self) -> str:
        # Safe only because the dispatcher is the sole consumer.
        while not self._job_queue:
            self._job_available.clear()
            await self._job_available.wait()
        return self._job_queue.popleft()

    def _release_slot_if_blocked(self, job: Job) -> None:
        """Hand a stuck job's capacity back to the fleet.

        Holding the slot would let one blocked buildout starve queued work that
        could run. Call only after _snapshot, since it reads the fresh state.
        """
        if job.id not in self._admitted:
            return
        if self.snapshots[job.id].state is not JobState.BLOCKED:
            return
        self._admitted.discard(job.id)
        self._job_slots.release()
        self.events.emit(now_min(), f"job {job.name} blocked, slot released", job.id)

    def _is_startable(self, job: Job, task_id: str) -> bool:
        return job.tasks[task_id].state is TaskState.PENDING and all(
            job.tasks[p].state is TaskState.FINISHED
            for p in job.workflow.parents(task_id)
        )

    def _collect_ready(
        self, job: Job, candidate_ids: tuple[str, ...]
    ) -> list[tuple[str, str]]:
        """Which of these tasks can be queued now. Marks them as enqueued.

        Gated on admission, not on started_at_min: a job that released its slot
        while blocked must not sneak tasks onto the queue when one is unblocked.
        """
        if job.id not in self._admitted:
            return []
        ready = []
        for task_id in candidate_ids:
            key = (job.id, task_id)
            if key in self._enqueued or not self._is_startable(job, task_id):
                continue
            self._enqueued.add(key)
            ready.append(key)
        return ready

    async def _dispatcher(self) -> None:
        while True:
            job_id = await self._next_job()
            await self._job_slots.acquire()

            job = self.jobs.get(job_id)
            if job is None:
                self._job_slots.release()
                continue

            self._admitted.add(job_id)
            if job.started_at_min is None:
                job.started_at_min = now_min()
                self.events.emit(now_min(), f"job {job.name} admitted", job_id)
            else:
                # Re-admitted after being blocked. started_at_min stays put, or
                # elapsed time and every delay figure would reset with it.
                self.events.emit(now_min(), f"job {job.name} resumed", job_id)

            ready = self._collect_ready(job, tuple(t.id for t in job.workflow.task_defs))
            self._snapshot(job)
            # It may have been unblocked into a state that still cannot run.
            self._release_slot_if_blocked(job)

            for item in ready:
                await self._task_queue.put(item)

    async def _worker(self) -> None:
        while True:
            job_id, task_id = await self._task_queue.get()

            job = self.jobs.get(job_id)
            self._enqueued.discard((job_id, task_id))
            if job is None:
                continue
            task = job.tasks[task_id]
            # It may have been blocked while sitting in the queue.
            if task.state is not TaskState.PENDING:
                continue
            task.state = TaskState.RUNNING
            task.started_at_min = now_min() - job.started_at_min
            duration = job.workflow.duration(task_id)
            self.events.emit(now_min(), f"{task_id} started", job_id)
            self._snapshot(job)

            await asyncio.sleep(_minutes_to_seconds(duration))

            job = self.jobs.get(job_id)
            if job is None:
                continue
            task = job.tasks[task_id]
            task.state = TaskState.FINISHED
            task.finished_at_min = now_min() - job.started_at_min
            self.events.emit(now_min(), f"{task_id} finished", job_id)
            ready = self._collect_ready(job, job.workflow.children(task_id))
            if all(t.state is TaskState.FINISHED for t in job.tasks.values()):
                job.finished_at_min = now_min()
                self._admitted.discard(job_id)
                self._job_slots.release()
                self.events.emit(now_min(), f"job {job.name} finished", job_id)
            self._snapshot(job)
            self._release_slot_if_blocked(job)

            for item in ready:
                await self._task_queue.put(item)

    async def _reconciler(self) -> None:
        interval = config.RECONCILE_INTERVAL_MS / 1000.0
        while True:
            await asyncio.sleep(interval)
            for job_id in list(self.jobs):
                job = self.jobs.get(job_id)
                if job is not None and job.finished_at_min is None:
                    self._snapshot(job)
                    self._release_slot_if_blocked(job)
                # Yield between jobs so a full registry cannot produce one long
                # synchronous stretch on the loop.
                await asyncio.sleep(0)
