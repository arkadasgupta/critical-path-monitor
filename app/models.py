"""Domain models.

Time convention: Task times are relative to job start, Job times are absolute
since service boot. Both in simulated minutes. Task times are relative so they
compare directly against the CPM numbers, which also start at 0.
"""

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    BLOCKED = "blocked"
    FINISHED = "finished"


class JobState(str, Enum):
    WAITING = "waiting"
    RUNNING = "running"
    BLOCKED = "blocked"
    FINISHED = "finished"


class TaskDef(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    duration_min: int = Field(gt=0)
    parent_task_ids: tuple[str, ...] = ()


class StaticSchedule(BaseModel):
    """CPM figures that depend only on the DAG, durations, and the baseline.

    None of these change while a job runs, so they are computed once per
    workflow rather than once per reconcile. Keeping them static is also what
    stops the critical path from flickering between reconciles.
    """

    model_config = ConfigDict(frozen=True)

    es: int
    ef: int
    ls: int
    lf: int
    buffer_min: int
    on_critical_path: bool


class WorkflowDef(BaseModel):
    """Build via criticality.build_workflow, which populates the derived fields
    below. Constructing this directly leaves it without a static schedule."""

    id: str
    name: str
    task_defs: tuple[TaskDef, ...]

    baseline_finish_min: int = 0
    task_defs_by_id: dict[str, TaskDef] = Field(default_factory=dict, repr=False)
    child_task_ids: dict[str, tuple[str, ...]] = Field(
        default_factory=dict, repr=False
    )
    topo_order: tuple[str, ...] = Field(default=(), repr=False)
    static: dict[str, StaticSchedule] = Field(default_factory=dict, repr=False)

    def model_post_init(self, __context: object) -> None:
        task_defs_by_id = {t.id: t for t in self.task_defs}
        if len(task_defs_by_id) != len(self.task_defs):
            raise ValueError(f"workflow {self.id} has duplicate task ids")
        for t in self.task_defs:
            for p in t.parent_task_ids:
                if p not in task_defs_by_id:
                    raise ValueError(f"task {t.id} references unknown parent {p}")

        adjacency: dict[str, list[str]] = {t.id: [] for t in self.task_defs}
        for t in self.task_defs:
            for p in t.parent_task_ids:
                adjacency[p].append(t.id)

        self.task_defs_by_id = task_defs_by_id
        self.child_task_ids = {k: tuple(v) for k, v in adjacency.items()}
        self.topo_order = _topo_sort(self.task_defs)

    def duration(self, task_id: str) -> int:
        return self.task_defs_by_id[task_id].duration_min

    def parents(self, task_id: str) -> tuple[str, ...]:
        return self.task_defs_by_id[task_id].parent_task_ids

    def children(self, task_id: str) -> tuple[str, ...]:
        return self.child_task_ids[task_id]


def _topo_sort(task_defs: tuple[TaskDef, ...]) -> tuple[str, ...]:
    indegree = {t.id: len(t.parent_task_ids) for t in task_defs}
    adjacency: dict[str, list[str]] = {t.id: [] for t in task_defs}
    for t in task_defs:
        for p in t.parent_task_ids:
            adjacency[p].append(t.id)

    # Seed in declaration order so graph layout is stable across runs.
    ready = [t.id for t in task_defs if indegree[t.id] == 0]
    order: list[str] = []
    while ready:
        node = ready.pop(0)
        order.append(node)
        for child in adjacency[node]:
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)

    if len(order) != len(task_defs):
        stuck = sorted(set(indegree) - set(order))
        raise ValueError(f"workflow graph has a cycle involving {stuck}")
    return tuple(order)


class Task(BaseModel):
    """BLOCKED is reachable only from PENDING. In-flight work runs to
    completion, so RUNNING and FINISHED tasks cannot be blocked."""

    id: str
    state: TaskState = TaskState.PENDING
    started_at_min: float | None = None
    finished_at_min: float | None = None


class Job(BaseModel):
    """Holds only facts. Job state, buffers, ETA and delay are derived by the
    reconciler into a JobSnapshot and never stored here."""

    id: str
    name: str
    workflow: WorkflowDef
    tasks: dict[str, Task]
    created_at_min: float
    started_at_min: float | None = None
    finished_at_min: float | None = None

    @property
    def baseline_finish_min(self) -> int:
        return self.workflow.baseline_finish_min


class TaskView(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    label: str
    duration_min: int
    state: TaskState
    stalled: bool
    startable: bool

    ls: int
    lf: int
    buffer_min: int
    on_critical_path: bool

    es: float
    ef: float
    buffer_remaining_min: float


class JobSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    name: str
    workflow_id: str
    workflow_name: str
    state: JobState
    baseline_finish_min: int
    projected_finish_min: float
    delay_incurred_min: float
    elapsed_min: float
    tasks: tuple[TaskView, ...]
    computed_at_min: float
    snapshot_seq: int
