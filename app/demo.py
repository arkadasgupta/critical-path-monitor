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
