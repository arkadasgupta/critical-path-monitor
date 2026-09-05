"""Deployment-time constants.

There is deliberately no config API. These are read once at import and surfaced
read-only through /api/state so the UI can display them.
"""

import os


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")
    return value


# Admission cap on total jobs held in memory. Creating beyond this returns 429.
MAX_JOBS = _int_env("MAX_JOBS", 8)

# How many jobs may be admitted from the job queue at once.
MAX_CONCURRENT_JOBS = _int_env("MAX_CONCURRENT_JOBS", 2)

# Size of the global task worker pool. Global rather than per-job on purpose:
# this is what makes concurrent jobs contend for the same capacity.
#
# Sized to fit the two seeded jobs rather than to squeeze them. A saturated pool
# delays tasks that are ready but have no worker, and that delay is real, so it
# lands in delay_incurred_min alongside delay caused by blocking. At 3 the two
# demo jobs accrue ~120 minutes of contention delay having been blocked by
# nobody, which drowns out the number the tool exists to report. At 6 they run
# essentially unimpeded and delay reads ~0 until someone blocks something.
#
# Set it to 3 to watch contention alone push both jobs late.
MAX_CONCURRENT_TASKS = _int_env("MAX_CONCURRENT_TASKS", 6)

# Real milliseconds per simulated minute. A 640-minute compute buildout
# completes in ~32s, storage in ~42s. Reviewers will not wait four real minutes.
TICK_MS = _int_env("TICK_MS", 50)

# How often the reconciler recomputes job snapshots. Job state is eventually
# consistent with this as the staleness bound.
RECONCILE_INTERVAL_MS = _int_env("RECONCILE_INTERVAL_MS", 250)


def as_dict() -> dict[str, int]:
    """Read-only view for the /api/state payload."""
    return {
        "max_jobs": MAX_JOBS,
        "max_concurrent_jobs": MAX_CONCURRENT_JOBS,
        "max_concurrent_tasks": MAX_CONCURRENT_TASKS,
        "tick_ms": TICK_MS,
        "reconcile_interval_ms": RECONCILE_INTERVAL_MS,
    }
