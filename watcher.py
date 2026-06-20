"""
Watcher: consumes trace events from agents and detects cost-vs-progress divergence.

Progress is measured ONLY by objective signals — no self-reported quality scores:
  - unique successful actions completed (distinct forward steps)
  - retry count on the same failed action
  - completion flag
  - elapsed time vs a soft expected-time threshold
"""

from dataclasses import dataclass, field
from typing import Dict, List
from agents import TraceEvent
from models import get_cheaper_alternatives, format_tradeoffs

ASSUMED_CURRENT_MODEL = "claude-opus-4-8"


RETRY_ALERT_THRESHOLD = 3
COST_STALL_THRESHOLD_USD = 0.05
EXPECTED_TASK_SECONDS = 15.0


@dataclass
class AgentState:
    agent_id: str
    events: List[TraceEvent] = field(default_factory=list)
    unique_successes: set = field(default_factory=set)
    max_retry_count: int = 0
    completed: bool = False
    alerted: bool = False
    start_time: float = field(default_factory=lambda: __import__('time').time())

    @property
    def cumulative_cost(self) -> float:
        return self.events[-1].cumulative_cost_usd if self.events else 0.0

    @property
    def progress_score(self) -> int:
        """Objective progress: count of distinct successful actions completed."""
        return len(self.unique_successes)

    @property
    def elapsed(self) -> float:
        return __import__('time').time() - self.start_time


@dataclass
class Alert:
    agent_id: str
    reason: str
    cost_usd: float
    retry_count: int
    progress_score: int
    recommendation: str


class Watcher:
    def __init__(self):
        self.states: Dict[str, AgentState] = {}
        self.alerts: List[Alert] = []

    def ingest(self, event: TraceEvent):
        if event.agent_id not in self.states:
            self.states[event.agent_id] = AgentState(agent_id=event.agent_id)

        state = self.states[event.agent_id]
        state.events.append(event)

        if event.success and not event.completed:
            state.unique_successes.add(event.action)
        if event.completed:
            state.completed = True
            state.unique_successes.add(event.action)
        if event.retry_count > state.max_retry_count:
            state.max_retry_count = event.retry_count

        self._evaluate(state, event)

    def _evaluate(self, state: AgentState, event: TraceEvent):
        if state.alerted or state.completed:
            return

        reasons = []

        if state.max_retry_count >= RETRY_ALERT_THRESHOLD and state.progress_score == 0:
            reasons.append(
                f"retried the same action {state.max_retry_count}x with zero successful steps"
            )

        if state.cumulative_cost >= COST_STALL_THRESHOLD_USD and state.progress_score == 0:
            reasons.append(
                f"spent ${state.cumulative_cost:.4f} with no progress"
            )

        if state.elapsed > EXPECTED_TASK_SECONDS and not state.completed and state.progress_score == 0:
            reasons.append(
                f"exceeded {EXPECTED_TASK_SECONDS}s time budget with no progress"
            )

        if reasons:
            state.alerted = True
            alternatives = get_cheaper_alternatives(ASSUMED_CURRENT_MODEL, 5.00)
            recommendation = (
                f"Pause {state.agent_id} — ${state.cumulative_cost:.4f} spent, "
                f"0 progress steps, {state.max_retry_count} retries. "
                "Debug the failing action before retrying.\n"
                + format_tradeoffs(alternatives)
            )
            alert = Alert(
                agent_id=state.agent_id,
                reason="; ".join(reasons),
                cost_usd=state.cumulative_cost,
                retry_count=state.max_retry_count,
                progress_score=state.progress_score,
                recommendation=recommendation,
            )
            self.alerts.append(alert)
            self._print_alert(alert)

    def _print_alert(self, alert: Alert):
        print(f"\n{'='*60}")
        print(f"  ALERT — {alert.agent_id}")
        print(f"  Reason      : {alert.reason}")
        print(f"  Cost so far : ${alert.cost_usd:.4f}")
        print(f"  Retries     : {alert.retry_count}")
        print(f"  Progress    : {alert.progress_score} steps completed")
        print(f"  Action      : {alert.recommendation}")
        print(f"{'='*60}\n")

    def summary(self):
        print("\n--- Final Summary ---")
        for agent_id, state in sorted(self.states.items()):
            status = "DONE" if state.completed else ("ALERTED" if state.alerted else "RUNNING")
            print(
                f"  {agent_id}: {status} | "
                f"cost=${state.cumulative_cost:.4f} | "
                f"progress={state.progress_score} steps | "
                f"retries={state.max_retry_count} | "
                f"elapsed={state.elapsed:.1f}s"
            )
        print()
