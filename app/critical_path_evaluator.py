"""Critical path evaluation.

The static pass (baseline, LS/LF, buffer, critical path) runs once per workflow
at import. The live pass (ES/EF, projection, delay) runs each reconcile against
actual task state.

The backward pass targets the baseline derived at import, never the live
projection. Targeting the live projection would slide the deadline forward with
every slip, buffer would be permanently zero, and delay_incurred_min would
always read 0.
"""

from app.models import (
    Job,
    JobSnapshot,
    JobState,
    StaticSchedule,
    TaskDef,
    TaskState,
    TaskView,
    WorkflowDef,
)


def build_workflow(
    workflow_id: str, name: str, task_defs: tuple[TaskDef, ...]
) -> WorkflowDef:
    """The sanctioned constructor: builds the DAG and attaches its schedule."""
    workflow = WorkflowDef(id=workflow_id, name=name, task_defs=task_defs)
    schedule, baseline = _static_pass(workflow)
    workflow.baseline_finish_min = baseline
    workflow.static = schedule
    return workflow


def _static_pass(workflow: WorkflowDef) -> tuple[dict[str, StaticSchedule], int]:
    # Forward pass: how early can each task possibly happen?
    #
    # A task cannot start until every one of its parents has finished, so its
    # earliest start is the LATEST of its parents' earliest finishes - the
    # slowest parent is the one that gates it. Tasks with no parents start at 0.
    #
    # max() rather than sum() is the whole point: sibling branches run in
    # parallel, so two parents taking 60 and 90 minutes gate the child at 90,
    # not 150. Walking in topological order guarantees every parent's EF is
    # already known by the time we reach the child.
    es: dict[str, int] = {}
    ef: dict[str, int] = {}
    for task_id in workflow.topo_order:
        parents = workflow.parents(task_id)
        es[task_id] = max((ef[p] for p in parents), default=0)
        ef[task_id] = es[task_id] + workflow.duration(task_id)

    # The job is done when its slowest chain is done. This is the baseline: the
    # delivery commitment every later measurement is taken against.
    baseline = max(ef.values())

    # Backward pass: how late can each task happen without moving that finish?
    #
    # The mirror image. A task must be finished before its children need to
    # start, so its latest finish is the EARLIEST of its children's latest
    # starts - the most urgent child is the one that constrains it. Tasks with
    # no children need only be done by the baseline. Subtracting the task's own
    # duration from its latest finish gives its latest start.
    #
    # Reverse topological order guarantees every child's LS is already known.
    ls: dict[str, int] = {}
    lf: dict[str, int] = {}
    for task_id in reversed(workflow.topo_order):
        children = workflow.children(task_id)
        lf[task_id] = min((ls[c] for c in children), default=baseline)
        ls[task_id] = lf[task_id] - workflow.duration(task_id)

    # Buffer is the gap between the two answers: how far a task's start can move
    # before it stops being free. Zero gap means the earliest it can happen is
    # also the latest it may happen - it is on the critical path, and any delay
    # there is immediately a delivery delay.
    schedule = {
        task_id: StaticSchedule(
            es=es[task_id],
            ef=ef[task_id],
            ls=ls[task_id],
            lf=lf[task_id],
            buffer_min=ls[task_id] - es[task_id],
            on_critical_path=ls[task_id] - es[task_id] == 0,
        )
        for task_id in workflow.topo_order
    }
    return schedule, baseline


def compute_snapshot(job: Job, now_min: float, snapshot_seq: int) -> JobSnapshot:
    """Derive the full view of a job from its stored facts.

    The same forward pass as above, but seeded from what actually happened
    rather than from zero. LS/LF are reused from the static schedule, since they
    depend only on the DAG, the durations and the fixed baseline - none of which
    move while the job runs.
    """
    workflow = job.workflow

    if job.started_at_min is None:
        elapsed = 0.0
    elif job.finished_at_min is not None:
        elapsed = job.finished_at_min - job.started_at_min
    else:
        elapsed = now_min - job.started_at_min

    es: dict[str, float] = {}
    ef: dict[str, float] = {}
    stalled: dict[str, bool] = {}
    startable: dict[str, bool] = {}

    for task_id in workflow.topo_order:
        task = job.tasks[task_id]
        parents = workflow.parents(task_id)
        duration = workflow.duration(task_id)
        parent_ef = max((ef[p] for p in parents), default=0.0)

        if task.state is TaskState.FINISHED:
            es[task_id] = (
                task.started_at_min if task.started_at_min is not None else parent_ef
            )
            ef[task_id] = (
                task.finished_at_min
                if task.finished_at_min is not None
                else es[task_id] + duration
            )
        elif task.state is TaskState.RUNNING:
            started = task.started_at_min if task.started_at_min is not None else elapsed
            es[task_id] = started
            remaining = max(0.0, duration - (elapsed - started))
            ef[task_id] = elapsed + remaining
        else:
            # PENDING or BLOCKED. Floored at `elapsed` because nothing can start
            # in the past. A blocked task is projected as if it clears this
            # instant, so its cost accrues as time advances rather than being
            # guessed at up front.
            es[task_id] = max(parent_ef, elapsed)
            ef[task_id] = es[task_id] + duration

        stalled[task_id] = any(
            job.tasks[p].state is TaskState.BLOCKED or stalled[p] for p in parents
        )
        startable[task_id] = task.state is TaskState.PENDING and all(
            job.tasks[p].state is TaskState.FINISHED for p in parents
        )

    projected_finish = max(ef.values())

    views = tuple(
        TaskView(
            id=task_id,
            label=workflow.task_defs_by_id[task_id].label,
            duration_min=workflow.duration(task_id),
            state=job.tasks[task_id].state,
            stalled=stalled[task_id],
            startable=startable[task_id],
            ls=workflow.static[task_id].ls,
            lf=workflow.static[task_id].lf,
            buffer_min=workflow.static[task_id].buffer_min,
            on_critical_path=workflow.static[task_id].on_critical_path,
            es=es[task_id],
            ef=ef[task_id],
            buffer_remaining_min=workflow.static[task_id].ls - es[task_id],
        )
        # Declaration order, not topo order: topo order is an implementation
        # detail and the UI wants a stable, human-authored ordering.
        for task_id in (t.id for t in workflow.task_defs)
    )

    return JobSnapshot(
        job_id=job.id,
        name=job.name,
        workflow_id=workflow.id,
        workflow_name=workflow.name,
        state=_job_state(job, startable),
        baseline_finish_min=workflow.baseline_finish_min,
        projected_finish_min=projected_finish,
        delay_incurred_min=projected_finish - workflow.baseline_finish_min,
        elapsed_min=elapsed,
        tasks=views,
        computed_at_min=now_min,
        snapshot_seq=snapshot_seq,
    )


def _job_state(job: Job, startable: dict[str, bool]) -> JobState:
    if job.finished_at_min is not None:
        return JobState.FINISHED
    if all(t.state is TaskState.FINISHED for t in job.tasks.values()):
        # The scheduler has not stamped finished_at_min yet. Reporting BLOCKED
        # here would be a visible wrong answer for one reconcile interval.
        return JobState.FINISHED
    if job.started_at_min is None:
        return JobState.WAITING
    running = any(t.state is TaskState.RUNNING for t in job.tasks.values())
    if not running and not any(startable.values()):
        return JobState.BLOCKED
    return JobState.RUNNING
