# SelfAudit

Runtime monitoring for AI agents. SelfAudit sits alongside your agent code and watches every action — tracking cost, retries, and progress in real time — so you can catch runaway loops and bad behavior before they spiral.

---

## The problem

AI agents fail silently. One stuck retry loop can burn through your API budget in minutes. By the time you notice something is wrong, you've already spent money you didn't need to.

SelfAudit gives you a live window into every agent you're running, with automatic alerts and actionable recommendations when things go sideways.

---

## Features

- **Live dashboard** — real-time view of all running agents, their cost, retry count, progress, and status
- **Automatic alerts** — fires when an agent exceeds cost, retry, or time thresholds
- **Smart recommendations** — context-aware suggestions: which model to switch to, which sub-task is failing, whether to pause or abort
- **Pause & intervene** — pause a runaway agent from the browser mid-run without touching your code
- **Review queue** — ambiguous agents (making some progress but burning cost) are surfaced for human judgment
- **Undo** — reverse recent interventions with one click
- **Redis-backed** — run agents on one machine, view the dashboard from anywhere

---

## Quickstart

```bash
git clone https://github.com/apoorva-khandelwal/selfaudit
cd selfaudit
pip install -r requirements.txt

# Add your credentials
cp .env.example .env

# Start the dashboard
python dashboard.py
# → http://localhost:5050

# In a separate terminal, run the example agents
python main.py
```

---

## Instrumenting your agent

```python
from sdk import Watcher

watcher = Watcher()
watcher.start_dashboard()  # starts dashboard at http://localhost:5050

# Wrap each action your agent takes
with watcher.trace("my-agent", action="summarize", model="claude-opus-4-8") as t:
    response = client.messages.create(...)
    t.success_from_anthropic(response)

# Mark failures so SelfAudit can count retries
with watcher.trace("my-agent", action="call_api") as t:
    if result.ok:
        t.success(cost_usd=0.001)
    else:
        t.fail()
```

That's it. SelfAudit starts alerting automatically once thresholds are exceeded.

---

## How alerts work

SelfAudit runs a background watcher that evaluates each agent on every event:

| Condition | What happens |
|---|---|
| Retries exceed threshold (default: 3) | Alert fired, agent flagged |
| Cost exceeds budget (default: $0.05) | Alert fired with model swap recommendation |
| High cost with low progress | Flagged for review |
| Agent stalled (no events for 30s) | Warning surfaced in dashboard |

Alerts can be **fixable** (watcher suggests a specific action) or **non-fixable** (requires human judgment). Non-fixable alerts trigger a browser notification even when you're on a different tab.

---

## Architecture

```
your agent code
     │
     ▼
  sdk.py (Watcher + trace context manager)
     │
     ├── in-memory state per agent
     ├── Redis pub/sub (for cross-machine sharing)
     │
     ▼
  dashboard.py (Flask + SSE)
     │
     ▼
  browser (live dashboard, no polling)
```

---

## Configuration

Set thresholds in the dashboard UI (Settings panel) or pass them directly:

```python
watcher = Watcher(
    retry_threshold=5,
    cost_threshold_usd=0.10,
    expected_task_seconds=60,
)
```

Per-agent cost caps can be set from the dashboard without restarting anything.

---

## Environment variables

```
ANTHROPIC_API_KEY=...
REDIS_HOST=...
REDIS_PORT=...
REDIS_USERNAME=...
REDIS_PASSWORD=...
```

---

## Built by

Apoorva Khandelwal — [apoorvakhandelwala@gmail.com](mailto:apoorvakhandelwala@gmail.com)
