# Working notes

Scratch file for picking the work back up. Feeds DESIGN.md later; not a deliverable itself.

## Time spent

**2h 00m** wall clock so far (14:33 -> 16:33, single session). Measured from the working
directory's creation time to the end of integration, not estimated.

| Phase | Span | Elapsed |
|---|---|---|
| Scoping, brainstorm, plan iteration | 14:33 - 15:28 | ~55m |
| Env setup (uv venv, py3.12) + model revisions | 15:28 - 15:46 | ~18m |
| Stage 0: evaluator, workflows, events, scheduler | 15:46 - 16:11 | ~25m |
| Stage 1: three subagents in parallel (routes/demo, frontend, tests) | 16:11 - 16:25 | ~14m |
| Stage 2: integration + end-to-end verification | 16:25 - 16:33 | ~8m |

Subagent cost detail: the three agents took 5m + 9m + 15m of their own runtime (~29m if run
serially) but finished in ~14m wall clock. Parallelism saved roughly 15 minutes; the frozen API
contract is what made that safe, and integration needed no rework.

Planning was the single largest line item at ~55m, against ~47m of actual construction.

## Still to do (deliberately deferred)

- README.md
- DESIGN.md rationale doc
- Deployment (not yet hosted anywhere)
- ~5 min explanation video
- `git init` + GitHub repo; nothing is under version control yet

## Carry into the writeup

- **The known limitation is narrower than first framed.** CPM cannot *predict* delay caused by
  worker-pool contention, but the live projection does *measure* it, because a task that is ready
  but has no free worker gets floored at `now` and its EF slides. Prediction gap, not a
  measurement gap.
- **Why MAX_CONCURRENT_TASKS defaults to 6, not 3.** At 3 the two seeded jobs accrued ~120
  simulated minutes of delay from contention alone, with nothing blocked, which buries the exact
  signal the tool exists to report. Measured: 3 -> ~120m, 4 -> ~48-78m, 6 -> ~2m, 8 -> ~2m.
  Set it to 3 to demonstrate contention deliberately.
- **A blocked job releases its concurrency slot** and re-enters the job queue at the *front* when
  unblocked, so it is not penalised twice for having been stuck.
- **Only pending tasks are blockable.** Consequence: a job reaches `blocked` only once its last
  in-flight task drains, so it settles rather than flipping instantly. Accepted tradeoff.
- **No locks in the scheduler**, and that is deliberate: every read-modify-write section is
  await-free, so cooperative scheduling makes it atomic. Documented at the top of scheduler.py.

## Verified behaviour (re-run before recording the video)

Free vs costly block, over HTTP, same gesture:
- `network_fabric_pull` (140m buffer): buffer drained 140 -> 8.9, job delay held at 0.0
- `power_whip_install` (0m buffer): buffer went negative at once, delay tracked it exactly
  (-131.0 buffer / +131.0 delay)

51 tests pass. `PYTHONPATH=. uv run pytest -q`
Run locally: `PYTHONPATH=. uv run uvicorn app.main:app --port 8000`
