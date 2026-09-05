# Critical Path Monitor

A workflow engine for datacenter rack buildouts that answers a question most
orchestrators don't:

> When a task gets blocked, is that block **free**, or is it **costing delivery
> time right now**?

Airflow and Temporal will tell you *that* something is blocked. Neither tells
you what the block *costs*. That gap is the whole point of this tool.

## The idea

Every task has a duration, so every task has a **buffer**: the number of minutes
it can sit blocked before the job as a whole finishes late. A task with 140
minutes of buffer can be blocked for two hours and nothing bad happens. A task
with zero buffer is on the **critical path**, and blocking it costs delivery
time from the first minute.

The engine derives both from the DAG using the two classic critical-path passes,
then projects them against what is actually happening right now.

## Try it

**→ https://critical-path-monitor.onrender.com/**

It is hosted on Render's free tier, which spins the service down when it is idle.
If it is cold, the first request wakes it and can take up to a minute — give it a
moment and reload. Every request after that is fast.

Nothing to set up. Two rack buildouts seed themselves and start running as soon
as the page loads, so you land on live state. Time is compressed: one simulated
minute is a few milliseconds, so a buildout that would take eleven hours
finishes in well under a minute. If everything has already finished, a banner
offers to run it again.

What you are looking at: each **node** is a task in the buildout, coloured by
state and outlined if it sits on the critical path. Each node shows its
**buffer** — how long it could be held up before delivery slips. The job list
shows each buildout's estimated finish against its committed baseline, and the
gap between them is the delay accrued so far.

**The whole product in two clicks.** Open a compute rack job and:

1. Block **Network fabric pull** (140m buffer). Its buffer drains, and the job's
   delay stays at **0**. The block is free.
2. Run the demo again, then block **Power whip install** (0m buffer, on the
   critical path). Delay starts climbing immediately, minute for minute.

Same gesture, opposite outcomes. That contrast is the thing worth looking at.

Also worth trying: block every runnable task and watch the job go `blocked` and
hand its concurrency slot back to the fleet, then unblock it and watch it jump
to the front of the queue.

**[DESIGN.md](DESIGN.md)** covers why this problem, what makes the approach
interesting, the design tradeoffs, and what I would build next.

**[transcripts/](transcripts/)** holds the full Claude Code session that built
this, including the four subagent sessions, converted from the raw logs to
readable markdown.

**[demo-trim.mov](demo-trim.mov)** is a short walkthrough of the idea and the
design decisions behind it.
