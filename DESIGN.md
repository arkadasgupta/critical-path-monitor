# Design rationale

**Live:** https://critical-path-monitor.onrender.com/ ·
**Code:** https://github.com/arkadasgupta/critical-path-monitor

A workflow engine for datacenter rack buildouts that answers a question the
usual orchestrators don't: when a task is blocked, is that block **free**, or is
it **costing delivery time right now**?

---

## Why this theme, and why this problem

I chose Theme 3 because the problem is one I've solved before in industry, and
it's the work I'm proudest of. Hardware buildout — turning delivered racks into
usable capacity — runs on workflow platforms that execute dependency graphs at
fleet scale, and detecting the critical path through them is the part I found
most interesting when I worked on it. This is a deliberately simplified,
self-contained version; the workflows, task names and durations are invented.

It fits Theme 3 because buildout orchestration is a systems problem before it is
a scheduling one: concurrency limits, work that stalls, and capacity that has to
be handed back when a job gets stuck.

## What's interesting

Answering *"what is this delay costing?"* needs no new information — a DAG with
durations already contains it. Two passes of critical path analysis give every
task a **buffer**: the minutes it can sit blocked before the job finishes late.

That distinction carries real operational weight. In a live buildout, a blocked
task on the critical path triggers a sev-1 or sev-2 incident; a task with buffer
to spare can wait. Knowing which one you are looking at is the difference
between paging someone and filing a ticket.

## Design decisions and tradeoffs

**Durations are fixed; in a real platform they're statistical.** Real platforms
estimate durations from historical runs and derive the delivery commitment from
those. Here durations are constants and the baseline is derived once and never
moves — which is what keeps the delay figure meaningful, at the cost of any
notion of confidence.

**Job and task queues simulate a multi-node executor.** One global task queue
drained by a fixed worker pool, rather than per-job queues, so concurrent jobs
contend for the same capacity the way they would across real workers. The pool
size is a simulation dial, not a real constraint: buildout capacity is treated
as unlimited in practice, which is why critical path analysis applies cleanly.

**Only pending tasks can be blocked.** In-flight work runs to completion, which
keeps the state machine small and matches real buildouts; the cost is that a job
settles into `blocked` rather than flipping the instant you block things.

**A blocked job hands back its concurrency slot** and re-enters the queue at the
front when unblocked, so a stuck buildout doesn't hold capacity but isn't
punished twice for having been stuck.

## Known limitations

- Delay is reported but not attributed to a cause.
- Tasks cannot fail or be retried; the only interruption modelled is an external
  block.
- State is in memory only; a restart reseeds rather than recovers.

## With more time

1. **Attribute delay to a cause** — re-project without a given block to price it
   individually, including across jobs.
2. **Statistical durations**, so buffer carries a confidence interval rather than
   being a single number.
3. **Failure and retry** as first-class task states.
4. **Persistence**, so a restart resumes rather than reseeds.

## Time spent

About **2 hours 45 minutes**: roughly 2 hours to a working, tested local build
and 45 minutes for deployment and documentation. Planning was the largest single
item at ~55 minutes against ~47 minutes of construction — implementation was
fanned out to parallel agents against an API contract frozen beforehand, so
integration needed no rework.
