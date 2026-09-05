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
        started_at_min=started_at,
        finished_at_min=finished_at,
    )


# --------------------------------------------------------------------------
# Baselines and buffers: exact regression anchors.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_id", sorted(EXPECTED))
def test_baseline_finish(workflow_id: str) -> None:
    expected_baseline, _ = EXPECTED[workflow_id]
    assert WORKFLOWS[workflow_id].baseline_finish_min == expected_baseline


@pytest.mark.parametrize("workflow_id", sorted(EXPECTED))
def test_buffers(workflow_id: str) -> None:
    workflow = WORKFLOWS[workflow_id]
    _, expected_buffers = EXPECTED[workflow_id]

    actual = {task_id: s.buffer_min for task_id, s in workflow.static.items()}
    assert actual == expected_buffers


@pytest.mark.parametrize("workflow_id", sorted(EXPECTED))
def test_static_schedule_covers_every_task(workflow_id: str) -> None:
    workflow = WORKFLOWS[workflow_id]
    assert set(workflow.static) == {t.id for t in workflow.task_defs}


@pytest.mark.parametrize("workflow_id", sorted(EXPECTED))
def test_on_critical_path_is_exactly_zero_buffer(workflow_id: str) -> None:
    workflow = WORKFLOWS[workflow_id]
    for task_id, schedule in workflow.static.items():
        assert schedule.on_critical_path == (schedule.buffer_min == 0), task_id


@pytest.mark.parametrize("workflow_id", sorted(EXPECTED))
def test_buffers_are_never_negative(workflow_id: str) -> None:
    workflow = WORKFLOWS[workflow_id]
    assert all(s.buffer_min >= 0 for s in workflow.static.values())


@pytest.mark.parametrize("workflow_id", sorted(EXPECTED))
def test_es_ef_and_ls_lf_are_internally_consistent(workflow_id: str) -> None:
    workflow = WORKFLOWS[workflow_id]
    for task_id, schedule in workflow.static.items():
        duration = workflow.duration(task_id)
        assert schedule.ef == schedule.es + duration, task_id
        assert schedule.ls == schedule.lf - duration, task_id
        # Buffer measured on the start edge equals buffer on the finish edge.
        assert schedule.buffer_min == schedule.lf - schedule.ef, task_id


@pytest.mark.parametrize("workflow_id", sorted(EXPECTED))
def test_forward_pass_respects_precedence(workflow_id: str) -> None:
    """A task's ES is exactly the max of its parents' EFs (0 for roots)."""
    workflow = WORKFLOWS[workflow_id]
    for task in workflow.task_defs:
        schedule = workflow.static[task.id]
        expected_es = max(
            (workflow.static[p].ef for p in task.parent_task_ids), default=0
        )
        assert schedule.es == expected_es, task.id


@pytest.mark.parametrize("workflow_id", sorted(EXPECTED))
def test_baseline_is_the_latest_earliest_finish(workflow_id: str) -> None:
    workflow = WORKFLOWS[workflow_id]
    assert workflow.baseline_finish_min == max(
        s.ef for s in workflow.static.values()
    )


@pytest.mark.parametrize("workflow_id", sorted(EXPECTED))
def test_critical_path_is_a_connected_chain_from_root_to_sink(
    workflow_id: str,
) -> None:
    workflow = WORKFLOWS[workflow_id]
    critical = [t.id for t in workflow.task_defs if workflow.static[t.id].on_critical_path]
    assert critical, "expected at least one critical task"

    chain = sorted(critical, key=lambda tid: workflow.static[tid].es)

    # Starts at a root, ends at a sink.
    assert workflow.parents(chain[0]) == ()
    assert workflow.children(chain[-1]) == ()

    # Every consecutive pair is a real parent -> child edge, and the chain is
    # gapless in time: each link starts exactly when the previous one finishes.
    for parent_id, child_id in zip(chain, chain[1:]):
        assert parent_id in workflow.parents(child_id), (parent_id, child_id)
        assert workflow.static[parent_id].ef == workflow.static[child_id].es

    # The chain accounts for the whole baseline.
    assert sum(workflow.duration(tid) for tid in chain) == workflow.baseline_finish_min


# --------------------------------------------------------------------------
# Live snapshots.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("workflow_id", sorted(EXPECTED))
def test_brand_new_job_projects_the_baseline(workflow_id: str) -> None:
    job = _new_job(workflow_id)
    snapshot = compute_snapshot(job, now_min=0.0, snapshot_seq=1)

    assert snapshot.state is JobState.WAITING
    assert snapshot.projected_finish_min == job.baseline_finish_min
    assert snapshot.delay_incurred_min == 0
    assert snapshot.elapsed_min == 0
    assert snapshot.snapshot_seq == 1
    assert {t.id for t in snapshot.tasks} == {t.id for t in job.workflow.task_defs}
    assert all(t.state is TaskState.PENDING for t in snapshot.tasks)
    assert all(not t.stalled for t in snapshot.tasks)


def test_brand_new_job_task_views_mirror_the_static_schedule() -> None:
    """With nothing started, the live ES/EF pass must reproduce the static one."""
    job = _new_job("compute_rack")
    snapshot = compute_snapshot(job, now_min=0.0, snapshot_seq=1)

    for view in snapshot.tasks:
        static = job.workflow.static[view.id]
        assert view.es == static.es, view.id
        assert view.ef == static.ef, view.id
        assert view.ls == static.ls
        assert view.lf == static.lf
        assert view.buffer_min == static.buffer_min
        assert view.on_critical_path == static.on_critical_path
        assert view.buffer_remaining_min == static.buffer_min, view.id


def test_task_views_keep_declaration_order() -> None:
    job = _new_job("storage_rack")
    snapshot = compute_snapshot(job, now_min=0.0, snapshot_seq=1)
    assert [t.id for t in snapshot.tasks] == [t.id for t in job.workflow.task_defs]


def test_startable_is_only_the_roots_before_anything_runs() -> None:
    job = _new_job("compute_rack", started=True)
    snapshot = compute_snapshot(job, now_min=0.0, snapshot_seq=1)
    startable = {t.id for t in snapshot.tasks if t.startable}
    assert startable == {"site_survey", "rack_delivery"}


def test_on_time_progress_incurs_no_delay() -> None:
    """Finishing the prep exactly on plan leaves the projection at baseline."""
    job = _new_job("compute_rack", started=True)
    _finish(job, "site_survey", 0, 30)
    _finish(job, "rack_delivery", 0, 60)
    _finish(job, "rack_placement", 60, 105)
    _finish(job, "power_whip_install", 30, 120)

    snapshot = compute_snapshot(job, now_min=120.0, snapshot_seq=2)

    assert snapshot.elapsed_min == 120
    assert snapshot.projected_finish_min == 640
    assert snapshot.delay_incurred_min == 0
    assert snapshot.state is JobState.RUNNING


def test_late_critical_task_moves_delay_by_exactly_its_slip() -> None:
    """power_whip_install has zero buffer, so 30 minutes late is 30 late."""
    job = _new_job("compute_rack", started=True)
    _finish(job, "site_survey", 0, 30)
    _finish(job, "rack_delivery", 0, 60)
    _finish(job, "rack_placement", 60, 105)
    _finish(job, "power_whip_install", 30, 150)  # planned EF 120, so 30 late

    snapshot = compute_snapshot(job, now_min=150.0, snapshot_seq=3)

    assert snapshot.elapsed_min == 150
    assert snapshot.projected_finish_min == 670
    assert snapshot.delay_incurred_min == 30

    power_whip = next(t for t in snapshot.tasks if t.id == "power_whip_install")
    assert power_whip.ef == 150
    assert power_whip.on_critical_path


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

    snapshot = compute_snapshot(job, now_min=330.0, snapshot_seq=5)

    assert snapshot.projected_finish_min == 680
    assert snapshot.delay_incurred_min == 40


def test_blocked_parent_marks_descendants_stalled() -> None:
    job = _new_job("compute_rack", started=True)
    _finish(job, "site_survey", 0, 30)
    job.tasks["power_whip_install"].state = TaskState.BLOCKED

    snapshot = compute_snapshot(job, now_min=40.0, snapshot_seq=6)
    stalled = {t.id for t in snapshot.tasks if t.stalled}

    assert "pdu_install" in stalled
    assert "gpu_node_install" in stalled
    assert "capacity_handoff" in stalled
    # The blocked task itself is not "stalled" - it is the cause, not a victim.
    assert "power_whip_install" not in stalled
    assert "network_fabric_pull" not in stalled


def test_blocked_critical_task_cost_accrues_with_elapsed_time() -> None:
    """A blocked task is projected as clearing now, so cost grows with time."""
    job = _new_job("compute_rack", started=True)
    _finish(job, "site_survey", 0, 30)
    _finish(job, "rack_delivery", 0, 60)
    _finish(job, "rack_placement", 60, 105)
    job.tasks["power_whip_install"].state = TaskState.BLOCKED

    early = compute_snapshot(job, now_min=100.0, snapshot_seq=7)
    later = compute_snapshot(job, now_min=200.0, snapshot_seq=8)

    # ls(power_whip_install) is 30, so at elapsed E the delay is E - 30.
    assert early.delay_incurred_min == 70
    assert later.delay_incurred_min == 170

    early_view = next(t for t in early.tasks if t.id == "power_whip_install")
    later_view = next(t for t in later.tasks if t.id == "power_whip_install")
    assert early_view.buffer_remaining_min == -70
    assert later_view.buffer_remaining_min == -170


def test_job_with_everything_finished_reads_finished_before_the_stamp() -> None:
    job = _new_job("compute_rack", started=True)
    for task in job.workflow.task_defs:
        _finish(job, task.id, 0, 1)

    snapshot = compute_snapshot(job, now_min=999.0, snapshot_seq=9)
    assert snapshot.state is JobState.FINISHED


def test_job_with_no_running_and_no_startable_task_reads_blocked() -> None:
    job = _new_job("compute_rack", started=True)
    job.tasks["site_survey"].state = TaskState.BLOCKED
    job.tasks["rack_delivery"].state = TaskState.BLOCKED

    snapshot = compute_snapshot(job, now_min=10.0, snapshot_seq=10)
    assert snapshot.state is JobState.BLOCKED


def test_finished_job_elapsed_freezes_at_the_finish_stamp() -> None:
    job = _new_job("compute_rack", started=True)
    for task in job.workflow.task_defs:
        _finish(job, task.id, 0, 1)
    job.finished_at_min = 700.0

    snapshot = compute_snapshot(job, now_min=5000.0, snapshot_seq=11)
    assert snapshot.elapsed_min == 700.0


# --------------------------------------------------------------------------
# Graph validation.
# --------------------------------------------------------------------------


def test_cyclic_graph_raises_on_construction() -> None:
    cyclic = (
        TaskDef(id="a", label="A", duration_min=10, parent_task_ids=("c",)),
        TaskDef(id="b", label="B", duration_min=10, parent_task_ids=("a",)),
        TaskDef(id="c", label="C", duration_min=10, parent_task_ids=("b",)),
    )
    with pytest.raises(ValueError, match="cycle"):
        build_workflow("cyclic", "Cyclic", cyclic)


def test_self_loop_raises_on_construction() -> None:
    with pytest.raises(ValueError, match="cycle"):
        build_workflow(
            "self_loop",
            "Self loop",
            (TaskDef(id="a", label="A", duration_min=10, parent_task_ids=("a",)),),
        )


def test_unknown_parent_raises_on_construction() -> None:
    with pytest.raises(ValueError, match="unknown parent"):
        build_workflow(
            "dangling",
            "Dangling",
            (TaskDef(id="a", label="A", duration_min=10, parent_task_ids=("nope",)),),
        )


def test_duplicate_task_ids_raise_on_construction() -> None:
    with pytest.raises(ValueError, match="duplicate task ids"):
        build_workflow(
            "dupes",
            "Dupes",
            (
                TaskDef(id="a", label="A", duration_min=10),
                TaskDef(id="a", label="A again", duration_min=20),
            ),
        )


def test_a_hand_built_diamond_has_the_expected_buffers() -> None:
    """Smallest graph where max() vs sum() in the forward pass is visible."""
    workflow = build_workflow(
        "diamond",
        "Diamond",
        (
            TaskDef(id="start", label="Start", duration_min=10),
            TaskDef(id="slow", label="Slow", duration_min=90, parent_task_ids=("start",)),
            TaskDef(id="fast", label="Fast", duration_min=60, parent_task_ids=("start",)),
            TaskDef(
                id="join",
                label="Join",
                duration_min=20,
                parent_task_ids=("slow", "fast"),
            ),
        ),
    )

    # Parallel branches, so the join is gated by 90, not 90 + 60.
    assert workflow.baseline_finish_min == 120
    assert workflow.static["slow"].buffer_min == 0
    assert workflow.static["fast"].buffer_min == 30
    assert workflow.static["start"].on_critical_path
    assert workflow.static["join"].on_critical_path
