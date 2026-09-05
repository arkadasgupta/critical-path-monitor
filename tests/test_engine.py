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
        "RECONCILE_INTERVAL_MS": 20,
        "MAX_JOBS": 8,
        "MAX_CONCURRENT_JOBS": 2,
        "MAX_CONCURRENT_TASKS": 16,
    }
    settings.update(overrides)
    for name, value in settings.items():
        monkeypatch.setattr(config, name, value)

    engine = Engine()
    supervisor = asyncio.create_task(engine.run())
    try:
        yield engine
    finally:
        supervisor.cancel()
        # A cancelled TaskGroup re-raises CancelledError; anything else that
        # comes out is a genuine failure inside the engine and should surface.
        with contextlib.suppress(asyncio.CancelledError):
            await supervisor


async def wait_until(
    predicate: Callable[[], T | None],
    *,
    timeout: float = 5.0,
    message: str = "",
) -> T:
    """Poll `predicate` until it returns something truthy, or fail the test."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        value = predicate()
        if value:
            return value
        if loop.time() >= deadline:
            pytest.fail(f"timed out after {timeout}s waiting for {message or predicate}")
        await asyncio.sleep(POLL_INTERVAL)


def snapshot_when(
    engine: Engine, job_id: str, condition: Callable[[JobSnapshot], bool]
) -> Callable[[], JobSnapshot | None]:
    def check() -> JobSnapshot | None:
        snapshot = engine.snapshots.get(job_id)
        return snapshot if snapshot is not None and condition(snapshot) else None

    return check


def task_view(snapshot: JobSnapshot, task_id: str):
    return next(t for t in snapshot.tasks if t.id == task_id)


def task_state(engine: Engine, job_id: str, task_id: str) -> TaskState:
    """Ground truth, no reconcile lag."""
    return engine.jobs[job_id].tasks[task_id].state


# --------------------------------------------------------------------------
# 1. Happy path.
# --------------------------------------------------------------------------


async def test_job_runs_to_completion(monkeypatch: pytest.MonkeyPatch) -> None:
    async with running_engine(monkeypatch) as engine:
        created = await engine.create_job("compute_rack", "happy-path")
        job_id = created.job_id
        assert created.state is JobState.WAITING
        assert created.delay_incurred_min == 0

        final = await wait_until(
            snapshot_when(engine, job_id, lambda s: s.state is JobState.FINISHED),
            timeout=COMPUTE_RACK_SECONDS * 4 + 5,
            message="job to finish",
        )

        assert all(t.state is TaskState.FINISHED for t in final.tasks)
        assert engine.jobs[job_id].finished_at_min is not None
        assert final.baseline_finish_min == 640
        # Nothing was blocked, so the only source of drift is loop overhead.
        assert 0 <= final.delay_incurred_min < 40, final.delay_incurred_min
        assert final.elapsed_min >= 640
        # The job gave its concurrency slot back on completion.
        assert job_id not in engine._admitted


# --------------------------------------------------------------------------
# 2 & 3. What a block actually costs.
# --------------------------------------------------------------------------


async def test_blocking_a_slack_task_burns_buffer_but_costs_no_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """network_fabric_pull has 140 minutes of buffer; hold it well under that."""
    async with running_engine(monkeypatch) as engine:
        created = await engine.create_job("compute_rack", "slack-block")
        job_id = created.job_id
        # No await between create and block, so the dispatcher cannot have
        # started the task yet - it is still PENDING and therefore blockable.
        blocked = await engine.block_task(job_id, "network_fabric_pull")
        assert task_view(blocked, "network_fabric_pull").state is TaskState.BLOCKED
        assert task_view(blocked, "network_fabric_pull").buffer_min == 140

        early = await wait_until(
            snapshot_when(engine, job_id, lambda s: s.elapsed_min >= 40),
            message="40 simulated minutes to elapse",
        )
        late = await wait_until(
            snapshot_when(engine, job_id, lambda s: s.elapsed_min >= 110),
            message="110 simulated minutes to elapse",
        )

        assert task_state(engine, job_id, "network_fabric_pull") is TaskState.BLOCKED

        early_view = task_view(early, "network_fabric_pull")
        late_view = task_view(late, "network_fabric_pull")

        # Buffer is visibly being eaten...
        assert late_view.buffer_remaining_min < early_view.buffer_remaining_min - 30
        assert late_view.buffer_remaining_min > 0
        # ...but the delivery date has not moved, because the block is off the
        # critical path and still inside its buffer.
        assert early.delay_incurred_min < 15, early.delay_incurred_min
        assert late.delay_incurred_min < 15, late.delay_incurred_min

        # Descendants of the block are reported as stalled.
        assert task_view(late, "tor_switch_install").stalled


async def test_blocking_a_critical_task_costs_delay_minute_for_minute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """power_whip_install has zero buffer, so the hold is pure delay."""
    async with running_engine(monkeypatch) as engine:
        created = await engine.create_job("compute_rack", "critical-block")
        job_id = created.job_id
        blocked = await engine.block_task(job_id, "power_whip_install")
        assert task_view(blocked, "power_whip_install").buffer_min == 0
        assert task_view(blocked, "power_whip_install").on_critical_path

        early = await wait_until(
            snapshot_when(engine, job_id, lambda s: s.elapsed_min >= 60),
            message="60 simulated minutes to elapse",
        )
        late = await wait_until(
            snapshot_when(engine, job_id, lambda s: s.elapsed_min >= 150),
            message="150 simulated minutes to elapse",
        )

        assert task_state(engine, job_id, "power_whip_install") is TaskState.BLOCKED

        # ls(power_whip_install) is 30, so delay tracks elapsed - 30.
        assert early.delay_incurred_min == pytest.approx(early.elapsed_min - 30, abs=10)
        assert late.delay_incurred_min == pytest.approx(late.elapsed_min - 30, abs=10)

        # Delay grows roughly in step with the hold.
        held = late.elapsed_min - early.elapsed_min
        grew = late.delay_incurred_min - early.delay_incurred_min
        assert grew == pytest.approx(held, abs=10), (held, grew)

        # Buffer on a zero-buffer task goes straight into deficit.
        assert task_view(early, "power_whip_install").buffer_remaining_min < 0
        assert task_view(late, "power_whip_install").buffer_remaining_min < -100


# --------------------------------------------------------------------------
# 4. Block/unblock state machine.
# --------------------------------------------------------------------------


async def test_blocking_a_running_task_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with running_engine(monkeypatch) as engine:
        job_id = (await engine.create_job("compute_rack", "running-block")).job_id

        await wait_until(
            lambda: task_state(engine, job_id, "site_survey") is TaskState.RUNNING,
            message="site_survey to start running",
        )
        with pytest.raises(TaskNotBlockable, match="running"):
            await engine.block_task(job_id, "site_survey")


async def test_blocking_a_finished_task_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with running_engine(monkeypatch) as engine:
        job_id = (await engine.create_job("compute_rack", "finished-block")).job_id

        await wait_until(
            lambda: task_state(engine, job_id, "site_survey") is TaskState.FINISHED,
            timeout=5.0,
            message="site_survey to finish",
        )
        with pytest.raises(TaskNotBlockable, match="finished"):
            await engine.block_task(job_id, "site_survey")


async def test_unblocking_a_task_that_is_not_blocked_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with running_engine(monkeypatch) as engine:
        job_id = (await engine.create_job("compute_rack", "bad-unblock")).job_id

        with pytest.raises(TaskNotBlocked, match="pending"):
            await engine.unblock_task(job_id, "site_survey")


async def test_block_then_unblock_round_trips_the_task_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with running_engine(monkeypatch) as engine:
        job_id = (await engine.create_job("compute_rack", "round-trip")).job_id

        await engine.block_task(job_id, "burn_in_test")
        assert task_state(engine, job_id, "burn_in_test") is TaskState.BLOCKED

        after = await engine.unblock_task(job_id, "burn_in_test")
        assert task_view(after, "burn_in_test").state is TaskState.PENDING

        with pytest.raises(TaskNotBlocked):
            await engine.unblock_task(job_id, "burn_in_test")


# --------------------------------------------------------------------------
# 5. A fully blocked job hands its concurrency slot back.
# --------------------------------------------------------------------------


async def test_blocked_job_releases_its_slot_and_is_readmitted_on_unblock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with running_engine(monkeypatch, MAX_CONCURRENT_JOBS=1) as engine:
        first = await engine.create_job("compute_rack", "stuck")
        stuck_id = first.job_id
        # The only two startable tasks. Blocked before the dispatcher can run
        # them, so the job has nothing at all it can do.
        await engine.block_task(stuck_id, "site_survey")
        await engine.block_task(stuck_id, "rack_delivery")

        stuck = await wait_until(
            snapshot_when(engine, stuck_id, lambda s: s.state is JobState.BLOCKED),
            message="job to report blocked",
        )
        assert all(not t.startable for t in stuck.tasks)
        await wait_until(
            lambda: stuck_id not in engine._admitted,
            message="blocked job to release its slot",
        )

        # The freed slot must be usable by other work.
        second_id = (await engine.create_job("compute_rack", "runner")).job_id
        await wait_until(
            lambda: second_id in engine._admitted,
            message="second job to be admitted into the freed slot",
        )
        assert stuck_id not in engine._admitted

        # Unblocking requeues the stuck job; it is re-admitted once the slot
        # frees up again, and then runs through to the end.
        await engine.unblock_task(stuck_id, "site_survey")
        await engine.unblock_task(stuck_id, "rack_delivery")
        assert stuck_id in engine._job_queue

        finished = await wait_until(
            snapshot_when(engine, stuck_id, lambda s: s.state is JobState.FINISHED),
            timeout=COMPUTE_RACK_SECONDS * 6 + 10,
            message="unblocked job to finish",
        )
        assert all(t.state is TaskState.FINISHED for t in finished.tasks)
        # started_at_min survived the block, so elapsed still spans the outage.
        assert engine.jobs[stuck_id].started_at_min is not None


# --------------------------------------------------------------------------
# 6. Task concurrency cap.
# --------------------------------------------------------------------------


async def test_max_concurrent_tasks_of_one_serializes_across_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with running_engine(
        monkeypatch, MAX_CONCURRENT_TASKS=1, MAX_CONCURRENT_JOBS=2
    ) as engine:
        await engine.create_job("compute_rack", "contender-a")
        await engine.create_job("storage_rack", "contender-b")

        loop = asyncio.get_running_loop()
        deadline = loop.time() + 2.0
        peak = 0
        samples = 0
        while loop.time() < deadline:
            running = sum(
                1
                for snapshot in list(engine.snapshots.values())
                for task in snapshot.tasks
                if task.state is TaskState.RUNNING
            )
            live = sum(
                1
                for job in list(engine.jobs.values())
                for task in job.tasks.values()
                if task.state is TaskState.RUNNING
            )
            assert running <= 1, f"{running} tasks running at once with a pool of 1"
            assert live <= 1, f"{live} tasks running at once with a pool of 1"
            peak = max(peak, running)
            samples += 1
            await asyncio.sleep(POLL_INTERVAL)

        assert samples > 50
        assert peak == 1, "never observed a running task; the sample proved nothing"


async def test_larger_task_pool_actually_runs_tasks_in_parallel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counterpart to the above: the cap is the thing doing the serializing."""
    async with running_engine(monkeypatch, MAX_CONCURRENT_TASKS=8) as engine:
        await engine.create_job("compute_rack", "wide-a")
        await engine.create_job("storage_rack", "wide-b")

        await wait_until(
            lambda: sum(
                1
                for job in list(engine.jobs.values())
                for task in job.tasks.values()
                if task.state is TaskState.RUNNING
            )
            >= 2,
            message="at least two tasks running concurrently",
        )


# --------------------------------------------------------------------------
# 7 & 8. Admission limits and lookup errors.
# --------------------------------------------------------------------------


async def test_max_jobs_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    async with running_engine(monkeypatch, MAX_JOBS=2) as engine:
        await engine.create_job("compute_rack", "one")
        await engine.create_job("storage_rack", "two")

        with pytest.raises(JobLimitReached):
            await engine.create_job("compute_rack", "three")

        assert len(engine.jobs) == 2


async def test_unknown_workflow_id_raises_key_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with running_engine(monkeypatch) as engine:
        with pytest.raises(KeyError):
            await engine.create_job("nonexistent_rack", "nope")
        assert engine.jobs == {}


async def test_unknown_job_and_task_ids_raise_key_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with running_engine(monkeypatch) as engine:
        job_id = (await engine.create_job("compute_rack", "lookups")).job_id

        with pytest.raises(KeyError):
            await engine.block_task("no-such-job", "site_survey")
        with pytest.raises(KeyError):
            await engine.unblock_task("no-such-job", "site_survey")
        with pytest.raises(KeyError):
            await engine.block_task(job_id, "no_such_task")
        with pytest.raises(KeyError):
            await engine.unblock_task(job_id, "no_such_task")


# --------------------------------------------------------------------------
# Event log.
# --------------------------------------------------------------------------


async def test_events_record_the_job_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with running_engine(monkeypatch) as engine:
        job_id = (await engine.create_job("compute_rack", "eventful")).job_id
        await engine.block_task(job_id, "network_validation")

        await wait_until(
            lambda: task_state(engine, job_id, "site_survey") is TaskState.FINISHED,
            message="site_survey to finish",
        )
        await engine.unblock_task(job_id, "network_validation")

        messages = [e.message for e in engine.events.since(0) if e.job_id == job_id]
        assert "job eventful queued" in messages
        assert "job eventful admitted" in messages
        assert "network_validation blocked" in messages
        assert "network_validation unblocked" in messages
        assert "site_survey started" in messages
        assert "site_survey finished" in messages
        # Sequence numbers are monotonic across the whole log.
        seqs = [e.seq for e in engine.events.since(0)]
        assert seqs == sorted(seqs)
