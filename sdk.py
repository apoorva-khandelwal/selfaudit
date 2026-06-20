"""
SelfAudit SDK — drop-in monitoring for any Python AI agent.

Quickstart:
    from selfaudit.sdk import Watcher

    watcher = Watcher()
    watcher.start_dashboard()          # http://localhost:5050

    with watcher.trace("my-agent", action="summarize") as t:
        response = client.messages.create(...)
        t.success_from_anthropic(response)

    with watcher.trace("my-agent", action="call_api") as t:
        if result.ok:
            t.success(cost_usd=0.001)
        else:
            t.fail()                   # watcher counts retries per action
"""

import os
import time
import threading
import datetime
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_ANTHROPIC_PRICES = {
    "claude-opus-4-8":   (5.00 / 1_000_000, 25.00 / 1_000_000),
    "claude-sonnet-4-6": (3.00 / 1_000_000, 15.00 / 1_000_000),
    "claude-haiku-4-5":  (1.00 / 1_000_000,  5.00 / 1_000_000),
}

RETRY_ALERT_THRESHOLD    = 3
COST_STALL_THRESHOLD_USD = 0.05
EXPECTED_TASK_SECONDS    = 30.0


# ── data model ─────────────────────────────────────────────────────────────────

@dataclass
class _Event:
    agent_id: str
    action: str
    cost_usd: float
    success: bool
    cumulative_cost_usd: float
    retry_count: int
    timestamp: float = field(default_factory=time.time)
    completed: bool = False


@dataclass
class _State:
    agent_id: str
    events: List[_Event] = field(default_factory=list)
    unique_successes: set = field(default_factory=set)
    retry_counts: Dict[str, int] = field(default_factory=dict)
    completed: bool = False
    alerted: bool = False
    peer_verdict: Optional[dict] = None
    start_time: float = field(default_factory=time.time)

    @property
    def cumulative_cost(self) -> float:
        return self.events[-1].cumulative_cost_usd if self.events else 0.0

    @property
    def progress_score(self) -> int:
        return len(self.unique_successes)

    @property
    def max_retry_count(self) -> int:
        return max(self.retry_counts.values(), default=0)

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time


@dataclass
class Alert:
    agent_id: str
    reason: str
    cost_usd: float
    retry_count: int
    progress_score: int
    recommendation: str
    timestamp: float = field(default_factory=time.time)


# ── trace handle ───────────────────────────────────────────────────────────────

class TraceHandle:
    def __init__(self, watcher: "Watcher", agent_id: str, action: str):
        self._watcher  = watcher
        self._agent_id = agent_id
        self._action   = action
        self._resolved = False

    def success(self, cost_usd: float = 0.0, completed: bool = False,
                output: str = ""):
        self._resolved = True
        self._watcher._record(self._agent_id, self._action,
                               cost_usd=cost_usd, success=True,
                               completed=completed, output=output)

    def success_from_anthropic(self, response, completed: bool = False):
        model = getattr(response, "model", "claude-opus-4-8")
        in_p, out_p = _ANTHROPIC_PRICES.get(model, (5e-6, 25e-6))
        usage = response.usage
        cost = usage.input_tokens * in_p + usage.output_tokens * out_p
        output = ""
        if response.content:
            output = getattr(response.content[0], "text", "")
        self.success(cost_usd=round(cost, 6), completed=completed, output=output)

    def fail(self, cost_usd: float = 0.0):
        self._resolved = True
        self._watcher._record(self._agent_id, self._action,
                               cost_usd=cost_usd, success=False,
                               completed=False, output="")

    def complete(self, cost_usd: float = 0.0, output: str = ""):
        self.success(cost_usd=cost_usd, completed=True, output=output)

    def _auto_fail_if_unresolved(self):
        if not self._resolved:
            self.fail()


# ── watcher ────────────────────────────────────────────────────────────────────

class Watcher:
    """
    Drop-in monitor for AI agents. Tracks cost vs. progress and alerts on divergence.
    """

    def __init__(self,
                 retry_threshold: int   = RETRY_ALERT_THRESHOLD,
                 cost_threshold:  float = COST_STALL_THRESHOLD_USD,
                 time_threshold:  float = EXPECTED_TASK_SECONDS,
                 task_description: str  = "",
                 peer_judge: bool       = False,
                 on_alert=None):
        self.states: Dict[str, _State] = {}
        self.alerts: List[Alert] = []
        self.peer_alerts: List[dict] = []
        self._lock             = threading.Lock()
        self._retry_threshold  = retry_threshold
        self._cost_threshold   = cost_threshold
        self._time_threshold   = time_threshold
        self._task_description = task_description
        self._peer_judge       = peer_judge and bool(os.environ.get("ANTHROPIC_API_KEY"))
        self._on_alert         = on_alert or self._print_alert

    # ── public API ─────────────────────────────────────────────────────────────

    @contextmanager
    def trace(self, agent_id: str, action: str):
        handle = TraceHandle(self, agent_id, action)
        try:
            yield handle
        except Exception:
            handle._auto_fail_if_unresolved()
            raise
        finally:
            handle._auto_fail_if_unresolved()

    def mark_complete(self, agent_id: str):
        with self._lock:
            if agent_id in self.states:
                self.states[agent_id].completed = True

    def start_dashboard(self, port: int = 5050):
        import dashboard as _dash
        _dash.start(self, port=port)

    def summary(self):
        print("\n--- SelfAudit Summary ---")
        with self._lock:
            for agent_id, state in sorted(self.states.items()):
                status = ("DONE"    if state.completed
                          else "ALERTED" if state.alerted
                          else "RUNNING")
                print(
                    f"  {agent_id}: {status} | "
                    f"cost=${state.cumulative_cost:.4f} | "
                    f"steps={state.progress_score} | "
                    f"retries={state.max_retry_count} | "
                    f"elapsed={state.elapsed:.1f}s"
                )
        print()

    # ── internals ──────────────────────────────────────────────────────────────

    def _record(self, agent_id, action, cost_usd, success, completed, output):
        with self._lock:
            if agent_id not in self.states:
                self.states[agent_id] = _State(agent_id=agent_id)

            state = self.states[agent_id]
            cumulative = state.cumulative_cost + cost_usd

            if not success:
                state.retry_counts[action] = state.retry_counts.get(action, 0) + 1
            else:
                state.retry_counts.pop(action, None)
                if not completed:
                    state.unique_successes.add(action)

            if completed:
                state.completed = True
                state.unique_successes.add(action)

            event = _Event(
                agent_id=agent_id,
                action=action,
                cost_usd=cost_usd,
                success=success,
                cumulative_cost_usd=round(cumulative, 6),
                retry_count=state.retry_counts.get(action, 0),
                completed=completed,
            )
            state.events.append(event)
            self._evaluate(state, event, output)

    def _evaluate(self, state: _State, event: _Event, output: str):
        if state.alerted or state.completed:
            return

        # ── numeric divergence check ───────────────────────────────────────────
        reasons = []

        if state.max_retry_count >= self._retry_threshold and state.progress_score == 0:
            reasons.append(
                f"retried same action {state.max_retry_count}x with zero successful steps"
            )
        if state.cumulative_cost >= self._cost_threshold and state.progress_score == 0:
            reasons.append(f"spent ${state.cumulative_cost:.4f} with no progress")

        if state.elapsed > self._time_threshold and state.progress_score == 0:
            reasons.append(f"exceeded {self._time_threshold:.0f}s with no progress")

        if reasons:
            self._fire_alert(state, "; ".join(reasons))
            return

        # ── peer judge (ambiguous zone only) ──────────────────────────────────
        if self._peer_judge and output and not state.peer_verdict:
            import peer_judge as pj
            if pj.should_peer_judge(state.progress_score, state.max_retry_count,
                                    state.cumulative_cost, self._cost_threshold):
                verdict = pj.judge(
                    agent_id=state.agent_id,
                    task_description=self._task_description,
                    recent_output=output,
                    progress_score=state.progress_score,
                    cumulative_cost=state.cumulative_cost,
                )
                state.peer_verdict = verdict
                if not verdict.get("on_task", True) and verdict.get("confidence") == "high":
                    self.peer_alerts.append({
                        "agent_id":       state.agent_id,
                        "reason":         verdict.get("reason", "off-task output detected"),
                        "recommendation": verdict.get("recommendation", "Review agent output."),
                        "time":           datetime.datetime.now().strftime("%H:%M:%S"),
                    })
                    print(f"\n[peer judge] {state.agent_id}: {verdict.get('reason')}")

    def _fire_alert(self, state: _State, reason: str):
        state.alerted = True
        from models import get_cheaper_alternatives, format_tradeoffs
        alts = get_cheaper_alternatives("claude-opus-4-8", 5.00)
        alert = Alert(
            agent_id=state.agent_id,
            reason=reason,
            cost_usd=state.cumulative_cost,
            retry_count=state.max_retry_count,
            progress_score=state.progress_score,
            recommendation=(
                f"Pause {state.agent_id} — ${state.cumulative_cost:.4f} spent, "
                f"{state.progress_score} steps, {state.max_retry_count} retries.\n"
                + format_tradeoffs(alts)
            ),
        )
        self.alerts.append(alert)
        self._on_alert(alert)

    def _print_alert(self, alert: Alert):
        print(f"\n{'='*60}")
        print(f"  ALERT — {alert.agent_id}")
        print(f"  Reason      : {alert.reason}")
        print(f"  Cost so far : ${alert.cost_usd:.4f}")
        print(f"  Retries     : {alert.retry_count}")
        print(f"  Progress    : {alert.progress_score} steps")
        for line in alert.recommendation.splitlines():
            print(f"  {line}")
        print(f"{'='*60}\n")
