"""
Drop SelfAudit into your existing agent in 3 lines.

Copy the pattern that matches your setup.
"""

# ─────────────────────────────────────────────────────────────
# PATTERN 1 — Anthropic SDK (cost extracted automatically)
# ─────────────────────────────────────────────────────────────
"""
import anthropic
from selfaudit.sdk import Watcher

client  = anthropic.Anthropic()
watcher = Watcher()
watcher.start_dashboard()   # http://localhost:5050

for step in my_agent_loop():
    with watcher.trace("my-agent", action=step.name) as t:
        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=1024,
            messages=[{"role": "user", "content": step.prompt}],
        )
        t.success_from_anthropic(response)          # cost extracted from response.usage
        # or mark the final step as done:
        t.success_from_anthropic(response, completed=True)
"""


# ─────────────────────────────────────────────────────────────
# PATTERN 2 — Any LLM / manual cost
# ─────────────────────────────────────────────────────────────
"""
from selfaudit.sdk import Watcher

watcher = Watcher()
watcher.start_dashboard()

with watcher.trace("my-agent", action="summarize") as t:
    result = my_llm.call(prompt)
    if result.ok:
        t.success(cost_usd=result.cost)
    else:
        t.fail(cost_usd=result.cost)   # watcher starts counting retries
"""


# ─────────────────────────────────────────────────────────────
# PATTERN 3 — Wrap an existing retry loop
# ─────────────────────────────────────────────────────────────
"""
from selfaudit.sdk import Watcher

watcher = Watcher(
    retry_threshold=3,       # alert after 3 failed retries
    cost_threshold=0.10,     # alert if $0.10 spent with no progress
    time_threshold=60.0,     # alert if 60s elapsed with no progress
)
watcher.start_dashboard()

for attempt in range(MAX_RETRIES):
    with watcher.trace("my-agent", action="call_api") as t:
        try:
            result = call_external_api()
            t.success(cost_usd=0.002)
            break
        except Exception:
            t.fail(cost_usd=0.002)
            # SelfAudit fires an alert and shows cheaper model alternatives
            # after retry_threshold failures — you decide whether to stop
"""


# ─────────────────────────────────────────────────────────────
# PATTERN 4 — Custom alert handler (e.g. Slack, PagerDuty)
# ─────────────────────────────────────────────────────────────
"""
from selfaudit.sdk import Watcher

def my_alert_handler(alert):
    slack.send(f"ALERT: {alert.agent_id} — {alert.reason} (${alert.cost_usd:.2f} spent)")
    # alert.recommendation includes model tradeoff table

watcher = Watcher(on_alert=my_alert_handler)
watcher.start_dashboard()
"""
