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

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import config, demo
from app.events import Event
from app.models import JobSnapshot
from app.scheduler import Engine, JobLimitReached, TaskNotBlockable, TaskNotBlocked

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
    jobs: list[JobSnapshot]


class StateResponse(BaseModel):
    config: dict[str, int]
    jobs: list[JobSnapshot]
    events: list[Event]
    seq: int


def _start_engine(app: FastAPI) -> None:
    app.state.engine = Engine()
    app.state.runner = asyncio.create_task(app.state.engine.run())


async def _stop_engine(app: FastAPI) -> None:
    runner: asyncio.Task | None = getattr(app.state, "runner", None)
    if runner is None:
        return
    runner.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await runner
    app.state.runner = None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    _start_engine(app)
    # Seed at startup so a cold start lands on a live, already-running demo
    # rather than an empty page that needs a button pressed first.
    await demo.seed(app.state.engine)
    try:
        yield
    finally:
        await _stop_engine(app)


app = FastAPI(title="Critical Path Monitor", lifespan=lifespan)


def _engine(request: Request) -> Engine:
    return request.app.state.engine


def _snapshot(engine: Engine, job_id: str) -> JobSnapshot:
    try:
        return engine.snapshots[job_id]
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}") from None


@app.post("/api/jobs", response_model=JobSnapshot)
async def create_job(request: Request, body: CreateJobRequest) -> JobSnapshot:
    engine = _engine(request)
    try:
        return await engine.create_job(body.workflow, body.name)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"unknown workflow {body.workflow}"
        ) from None
    except JobLimitReached as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None


@app.get("/api/jobs", response_model=list[JobSnapshot])
async def list_jobs(request: Request) -> list[JobSnapshot]:
    engine = _engine(request)
    return [
        engine.snapshots[job_id]
        for job_id in engine.jobs
        if job_id in engine.snapshots
    ]


@app.get("/api/jobs/{job_id}", response_model=JobDetail)
async def get_job(request: Request, job_id: str) -> JobDetail:
    engine = _engine(request)
    job = engine.jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"unknown job {job_id}")
    snapshot = _snapshot(engine, job_id)
    edges = tuple(
        Edge(source=parent, target=task_def.id)
        for task_def in job.workflow.task_defs
        for parent in task_def.parent_task_ids
    )
    return JobDetail(**snapshot.model_dump(), edges=edges)


@app.post("/api/jobs/{job_id}/tasks/{task_id}/block", response_model=JobSnapshot)
async def block_task(request: Request, job_id: str, task_id: str) -> JobSnapshot:
    engine = _engine(request)
    try:
        return await engine.block_task(job_id, task_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"unknown job {job_id} or task {task_id}"
        ) from None
    except TaskNotBlockable as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.post("/api/jobs/{job_id}/tasks/{task_id}/unblock", response_model=JobSnapshot)
async def unblock_task(request: Request, job_id: str, task_id: str) -> JobSnapshot:
    engine = _engine(request)
    try:
        return await engine.unblock_task(job_id, task_id)
    except KeyError:
        raise HTTPException(
            status_code=404, detail=f"unknown job {job_id} or task {task_id}"
        ) from None
    except TaskNotBlocked as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None


@app.post("/api/demo/run", response_model=DemoRunResponse)
async def run_demo(request: Request) -> DemoRunResponse:
    """Reset to a fresh demo.

    Reset is replacement, not cleanup: the old engine's supervisor task is
    cancelled and a new Engine is built. Draining the task queue and resetting
    the semaphore in place would leave in-flight worker sleeps writing into
    state that is supposed to be gone.
    """
    fastapi_app = request.app
    await _stop_engine(fastapi_app)
    _start_engine(fastapi_app)
    jobs = await demo.seed(fastapi_app.state.engine)
    return DemoRunResponse(ok=True, jobs=jobs)


@app.get("/api/state", response_model=StateResponse)
async def get_state(request: Request, since: int = Query(0, ge=0)) -> StateResponse:
    engine = _engine(request)
    return StateResponse(
        config=config.as_dict(),
        jobs=[
            engine.snapshots[job_id]
            for job_id in engine.jobs
            if job_id in engine.snapshots
        ],
        events=engine.events.since(since),
        seq=engine.events.seq,
    )


# Mounted last: a mount at "/" swallows every path below it, so it must be
# registered after the API routes.
_ensure_static_dir()
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
