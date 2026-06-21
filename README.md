# selfaudit

**Catches AI agents that spend without progress.**

A monitoring layer for AI agents that tracks cost against actual progress — not just total spend — and flags the moment an agent is burning money without producing anything. Built around a real, widely-cited industry incident: four agents stuck in a retry loop for 11 days, $47,000 in API charges before anyone noticed.

## The problem

Every existing cost-monitoring tool answers one question: *how much is this agent spending?* None of them answer the more important one — *is that spend buying anything?* An agent can stay well under budget every single time and still be a complete waste, because the failure mode isn't "too expensive," it's "spending with nothing to show for it." That's invisible to a tool that only watches dollars.

## What it does

- Tracks **cost vs. progress** for any agent, using only objective signals: distinct successful steps, retry counts on failed actions, elapsed time against an expected baseline. The agent never grades its own work — that's an intentional design choice (see [Design decisions](#design-decisions)).
- When cost climbs while progress stays flat, it **flags the agent and surfaces a recommendation** — including cheaper-model alternatives with published pricing — instead of auto-killing anything. A human decides whether to pause, re-run, or escalate.
- Ambiguous cases (some progress, but elevated retries or cost) go to a **review queue** instead of firing a false alarm, so the system distinguishes "definitely stuck" from "worth a second look."
- Every alert is **embedded and stored in Redis**; future alerts are checked against similar past cases instead of being judged cold each time.
- Dashboard state is **pushed through Redis pub/sub**, so a second dashboard instance on a different machine can mirror the same live data with no direct connection to the process running the agents.

## Demo

```bash
python main.py
```
Opens a live dashboard at `http://localhost:5050` and runs five simulated agents (healthy, stuck, slow, fast-failing, and an ambiguous one that makes partial progress before stalling) to exercise every code path.

To run a second dashboard reading purely from Redis (useful to demo the cross-machine sync):
```bash
python dashboard.py 5051
```

## Setup

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file (never committed — see `.env.example` for the required keys):
```
REDIS_HOST=...
REDIS_PORT=...
REDIS_USERNAME=...
REDIS_PASSWORD=...
ANTHROPIC_API_KEY=...
```

## Architecture

```
sdk.py        — Watcher class: drop-in SDK (`with watcher.trace(agent_id, action) as t:`)
dashboard.py  — Flask app, SSE stream, live UI
memory.py     — Redis-backed alert memory (embed → store → retrieve similar past alerts)
redis_store.py— Cross-machine dashboard state via Redis snapshot + pub/sub
models.py     — Static model cost/spec lookup table, used for "cheaper alternative" recommendations
main.py       — Demo runner: simulates 5 agent behaviors against the Watcher
```

## Design decisions

**Why doesn't the agent judge its own work?** We considered it and rejected it. An agent self-reporting "I'm doing fine" has the same structural problem as a student grading their own exam — no real incentive to flag its own failure. Instead, every signal SelfAudit uses is something you can count, not something you have to ask an LLM to judge: completed steps, retry counts, elapsed time.

**Why human approval instead of auto-retry?** A stuck agent re-run blindly just repeats the same failure and burns more money — the exact problem we're trying to prevent. SelfAudit surfaces a recommendation; a human (or your own approval logic) decides.

## Built with

Python · Flask · Server-Sent Events · Redis (alert memory + cross-machine pub/sub) · Anthropic API · vanilla JavaScript

## Known limitations

Being upfront about scope, since a hackathon judge will appreciate honesty over a polished overclaim:
- Embeddings used for Redis similarity search are a lightweight character-trigram hash, not a trained semantic model — "similar" means textually similar, not deeply semantic.
- An earlier LLM-based peer-review prototype exists in the repo but isn't wired into the live path; the current design uses a human review queue instead (see [Design decisions](#design-decisions)).
- Agent behaviors are simulated for demo purposes, not connected to a live production agent — integration with a real agent loop is a 3-line drop-in (see `examples.py`).
