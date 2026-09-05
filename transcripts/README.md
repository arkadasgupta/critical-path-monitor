# AI development transcripts

The full Claude Code session that produced this project, converted from the raw
JSONL logs into readable markdown by [`jsonl_to_markdown.py`](jsonl_to_markdown.py).

| File | What it covers | Messages |
|---|---|---|
| [00-main-session.md](00-main-session.md) | The whole build: scoping, design, the critical path evaluator and scheduler, integration, deployment, docs | 368 |
| [01-agent-api-routes.md](01-agent-api-routes.md) | Subagent — FastAPI routes and demo seeding | 51 |
| [02-agent-frontend.md](02-agent-frontend.md) | Subagent — the single-page UI | 115 |
| [03-agent-tests.md](03-agent-tests.md) | Subagent — the 51-test suite | 55 |
| [04-agent-deploy-verification.md](04-agent-deploy-verification.md) | Subagent — verifying the live deployment | 28 |

Speaker turns are marked `## ‹User›` and `## ‹Claude›`, so the transcripts can
be searched or split on those.

## How the work was split

The main session is where the decisions were made. Implementation was fanned out
to three subagents working in parallel against an API contract frozen before any
of them started — the contract is what let three agents write against the same
system without conflicting conventions, and integration needed no rework.

The critical path evaluator and the scheduler were deliberately **not**
delegated: they carry the actual idea, and a plausible-but-wrong answer in
either is more expensive to diagnose than to write.

## What was edited

Nothing was added or reworded. Two omissions, both mechanical:

- **Model reasoning blocks are excluded.** These are the assistant's internal
  monologue rather than conversation.
- **Long tool outputs are truncated** at 2500 characters, with the original
  length noted inline.

Raw JSONL is preserved locally under `~/.claude/projects/` if the unabridged
record is wanted.
