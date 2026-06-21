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
            t.fail()
"""

import os
import time
import threading
import datetime
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from models import MODELS, get_cheaper_alternatives, format_tradeoffs

_ANTHROPIC_PRICES = {
    "claude-opus-4-8":   (5.00 / 1_000_000, 25.00 / 1_000_000),
    "claude-sonnet-4-6": (3.00 / 1_000_000, 15.00 / 1_000_000),
    "claude-haiku-4-5":  (1.00 / 1_000_000,  5.00 / 1_000_000),
}

def _model_rec(model: Optional[str]) -> str:
    m = next((x for x in MODELS if x["id"] == model), None)
    price = m["input_cost_per_1m"] if m else 5.00
    alts = get_cheaper_alternatives(model or "claude-opus-4-8", price)
    return format_tradeoffs(alts)


def _build_recommendation(agent_id: str, model: Optional[str],
                           cost: float, retries: int, progress: int,
                           situation: str) -> dict:
    """Build context-aware recommendation. Returns a dict for structured rendering."""
    m = next((x for x in MODELS if x["id"] == model), None)
    price = m["input_cost_per_1m"] if m else 5.00
    alts = get_cheaper_alternatives(model or "claude-opus-4-8", price)
    model_label = model or "unknown model"

    if situation == "zero_progress":
        headline = f"No progress after {retries} retries — ${cost:.4f} spent on {model_label}"
        steps = [
            "Fix the underlying issue first (bad prompt, API error, logic bug) — retrying a broken agent wastes money.",
            f"Debug on a cheaper model. Don't spend {model_label} rates diagnosing the problem.",
        ]
    elif situation == "stuck_subtask":
        headline = f"{progress} step(s) succeeded, then got stuck — {retries} retries on the same sub-task"
        steps = [
            "The problem is isolated. Earlier steps don't need to re-run — only the failing sub-task needs fixing.",
            f"Consider a cheaper model just for the failing sub-task; keep {model_label} for the steps that need it.",
        ]
    elif situation == "high_cost_ratio":
        headline = f"High cost per step — ${cost:.4f} for {progress} step(s) on {model_label}"
        steps = [
            "Profile which steps consume the most tokens — usually a small number of calls drive most of the cost.",
            f"Route cheap/repetitive steps to a cheaper model; reserve {model_label} for the complex ones.",
        ]
    else:
        headline = f"Review {agent_id} — ${cost:.4f}, {progress} steps, {retries} retries on {model_label}"
        steps = []

    return {"headline": headline, "steps": steps, "alternatives": alts}

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
    total_successes: int = 0
    retry_counts: Dict[str, int] = field(default_factory=dict)
    completed: bool = False
    alerted: bool = False
    paused: bool = False
    flagged: bool = False
    notes: List[str] = field(default_factory=list)
    peer_verdict: Optional[dict] = None
    model: Optional[str] = None          # last model used by this agent
    budget_usd: Optional[float] = None   # hard cap; None = no cap
    progress_mode: str = "unique"        # "unique" or "total"
    retry_threshold: Optional[int] = None    # per-agent override; None = use global
    cost_threshold: Optional[float] = None
    time_threshold: Optional[float] = None
    start_time: float = field(default_factory=time.time)

    @property
    def cumulative_cost(self) -> float:
        return self.events[-1].cumulative_cost_usd if self.events else 0.0

    @property
    def progress_score(self) -> int:
        return self.total_successes if self.progress_mode == "total" else len(self.unique_successes)

    @property
    def max_retry_count(self) -> int:
        return max(self.retry_counts.values(), default=0)

    @property
    def cost_rate_per_min(self) -> float:
        """Average spend per minute since start."""
        elapsed_min = self.elapsed / 60.0
        return (self.cumulative_cost / elapsed_min) if elapsed_min > 0.01 else 0.0

    @property
    def projected_cost_1h(self) -> float:
        return self.cost_rate_per_min * 60.0

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
    recommendation: dict
    id: str = field(default_factory=lambda: str(time.time()))
    timestamp: float = field(default_factory=time.time)
    dismissed: bool = False


# ── trace handle ───────────────────────────────────────────────────────────────

class TraceHandle:
    def __init__(self, watcher: "Watcher", agent_id: str, action: str, model: str = None):
        self._watcher  = watcher
        self._agent_id = agent_id
        self._action   = action
        self._model    = model
        self._resolved = False

    def success(self, cost_usd: float = 0.0, completed: bool = False, output: str = ""):
        self._resolved = True
        if self._model:
            self._watcher._set_model(self._agent_id, self._model)
        self._watcher._record(self._agent_id, self._action,
                               cost_usd=cost_usd, success=True,
                               completed=completed, output=output)

    def success_from_anthropic(self, response, completed: bool = False):
        model = getattr(response, "model", "claude-opus-4-8")
        in_p, out_p = _ANTHROPIC_PRICES.get(model, (5e-6, 25e-6))
        usage = response.usage
        cost = usage.input_tokens * in_p + usage.output_tokens * out_p
        output = getattr(response.content[0], "text", "") if response.content else ""
        self._watcher._set_model(self._agent_id, model)
        self.success(cost_usd=round(cost, 6), completed=completed, output=output)

    def fail(self, cost_usd: float = 0.0):
        self._resolved = True
        if self._model:
            self._watcher._set_model(self._agent_id, self._model)
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
                 retry_threshold:  int   = RETRY_ALERT_THRESHOLD,
                 cost_threshold:   float = COST_STALL_THRESHOLD_USD,
                 time_threshold:   float = EXPECTED_TASK_SECONDS,
                 task_description: str   = "",
                 progress_mode:    str   = "unique",
                 on_alert=None):
        self.states: Dict[str, _State] = {}
        self.alerts: List[Alert] = []
        self.cost_saved_usd: float = 0.0  # estimated savings from pausing alerted agents
        self._lock             = threading.Lock()
        self._dirty            = threading.Event()
        self._retry_threshold  = retry_threshold
        self._cost_threshold   = cost_threshold
        self._time_threshold   = time_threshold
        self._task_description = task_description
        self._progress_mode    = progress_mode
        self._on_alert         = on_alert or self._print_alert

    # ── public control API ─────────────────────────────────────────────────────

    @contextmanager
    def trace(self, agent_id: str, action: str, model: str = None):
        handle = TraceHandle(self, agent_id, action, model=model)
        try:
            yield handle
        except Exception:
            handle._auto_fail_if_unresolved()
            raise
        finally:
            handle._auto_fail_if_unresolved()

    def pause(self, agent_id: str):
        with self._lock:
            if agent_id in self.states:
                self.states[agent_id].paused = True
        self._dirty.set()

    def resume(self, agent_id: str):
        with self._lock:
            if agent_id in self.states:
                self.states[agent_id].paused = False
        self._dirty.set()

    def flag(self, agent_id: str):
        """Mark an agent for human review."""
        with self._lock:
            if agent_id in self.states:
                self.states[agent_id].flagged = True
        self._dirty.set()

    def unflag(self, agent_id: str):
        with self._lock:
            if agent_id in self.states:
                self.states[agent_id].flagged = False
        self._dirty.set()

    def escalate_flag(self, agent_id: str):
        """Human reviewed the flag and confirmed the agent is broken — fire a full alert."""
        with self._lock:
            state = self.states.get(agent_id)
            if not state or state.alerted or state.completed:
                return
            state.alerted = True
            state.flagged = False
            cost     = state.cumulative_cost
            retries  = state.max_retry_count
            progress = state.progress_score
        self._fire_alert(agent_id, "escalated by human reviewer after flag", cost, retries, progress)
        self._dirty.set()

    def add_note(self, agent_id: str, note: str):
        with self._lock:
            if agent_id not in self.states:
                self.states[agent_id] = self._new_state(agent_id)
            self.states[agent_id].notes.append(
                f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {note}"
            )
        self._dirty.set()

    def edit_note(self, agent_id: str, index: int, new_text: str) -> bool:
        with self._lock:
            state = self.states.get(agent_id)
            if not state or index < 0 or index >= len(state.notes):
                return False
            # preserve timestamp prefix, replace the text after it
            old = state.notes[index]
            prefix = old[:old.index("] ") + 2] if "] " in old else ""
            state.notes[index] = prefix + new_text
        self._dirty.set()
        return True

    def delete_note(self, agent_id: str, index: int) -> bool:
        with self._lock:
            state = self.states.get(agent_id)
            if not state or index < 0 or index >= len(state.notes):
                return False
            state.notes.pop(index)
        self._dirty.set()
        return True

    def dismiss_alert(self, alert_id: str):
        with self._lock:
            for a in self.alerts:
                if a.id == alert_id:
                    a.dismissed = True
                    break
        self._dirty.set()

    def undismiss_alert(self, alert_id: str):
        with self._lock:
            for a in self.alerts:
                if a.id == alert_id:
                    a.dismissed = False
                    break
        self._dirty.set()

    def delete_alert(self, alert_id: str):
        with self._lock:
            self.alerts = [a for a in self.alerts if a.id != alert_id]
        self._dirty.set()

    def restore_alert(self, alert: "Alert"):
        with self._lock:
            if not any(a.id == alert.id for a in self.alerts):
                self.alerts.append(alert)
                self.alerts.sort(key=lambda a: a.timestamp)
        self._dirty.set()

    def set_budget(self, agent_id: str, budget_usd: float) -> bool:
        """Set a hard cost cap for an agent. Returns False if agent doesn't exist yet."""
        with self._lock:
            if agent_id not in self.states:
                return False
            self.states[agent_id].budget_usd = budget_usd
        self._dirty.set()
        return True

    def set_thresholds(self, retry: int = None, cost: float = None, time: float = None):
        """Update global detection thresholds — applies to all agents without per-agent overrides."""
        with self._lock:
            if retry is not None: self._retry_threshold = retry
            if cost  is not None: self._cost_threshold  = cost
            if time  is not None: self._time_threshold  = time
        self._dirty.set()

    def set_agent_thresholds(self, agent_id: str, retry: int = None, cost: float = None, time: float = None) -> bool:
        """Set per-agent threshold overrides. Returns False if agent doesn't exist."""
        with self._lock:
            if agent_id not in self.states:
                return False
            state = self.states[agent_id]
            if retry is not None: state.retry_threshold = retry
            if cost  is not None: state.cost_threshold  = cost
            if time  is not None: state.time_threshold  = time
        self._dirty.set()
        return True

    def _set_model(self, agent_id: str, model: str):
        with self._lock:
            if agent_id not in self.states:
                self.states[agent_id] = self._new_state(agent_id)
            self.states[agent_id].model = model

    def mark_complete(self, agent_id: str):
        with self._lock:
            if agent_id in self.states:
                self.states[agent_id].completed = True
        self._dirty.set()

    def _push_to_redis(self):
        """Push current snapshot to Redis for cross-machine sharing. Silent if unavailable."""
        try:
            import dashboard as _dash
            import redis_store as _rs
            snap = _dash._snapshot(self)
            _rs.push_snapshot(snap)
        except Exception:
            pass

    def start_dashboard(self, port: int = 5050):
        import dashboard as _dash
        _dash.start(self, port=port)

    def summary(self):
        print("\n--- SelfAudit Summary ---")
        with self._lock:
            for agent_id, state in sorted(self.states.items()):
                status = ("PAUSED"  if state.paused
                          else "DONE"    if state.completed
                          else "ALERTED" if state.alerted
                          else "FLAGGED" if state.flagged
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

    def _new_state(self, agent_id: str) -> "_State":
        return _State(agent_id=agent_id, progress_mode=self._progress_mode)

    def _record(self, agent_id, action, cost_usd, success, completed, output):
        with self._lock:
            if agent_id not in self.states:
                self.states[agent_id] = self._new_state(agent_id)

            state = self.states[agent_id]

            # Don't process events for paused agents
            if state.paused:
                return
            budget_hit = False

            cumulative = state.cumulative_cost + cost_usd

            if not success:
                state.retry_counts[action] = state.retry_counts.get(action, 0) + 1
            else:
                state.retry_counts.pop(action, None)
                state.total_successes += 1
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

            # budget cap — auto-pause and estimate savings
            budget_hit = (
                state.budget_usd is not None
                and state.cumulative_cost >= state.budget_usd
                and not state.paused and not state.completed
            )
            if budget_hit:
                state.paused = True
                # estimate savings: what it would have spent at current rate for 1h more
                with_savings = state.projected_cost_1h
                self.cost_saved_usd += with_savings

            should_eval   = not state.alerted and not state.completed
            snap_retries  = state.max_retry_count
            snap_cost     = state.cumulative_cost
            snap_progress = state.progress_score
            snap_elapsed  = state.elapsed
            snap_budget   = state.budget_usd

        self._dirty.set()
        threading.Thread(target=self._push_to_redis, daemon=True).start()

        if budget_hit:
            print(f"\n[watcher] {agent_id} auto-paused — hit budget cap of ${snap_budget:.2f}")

        if should_eval:
            self._evaluate(agent_id, snap_retries, snap_cost,
                           snap_progress, snap_elapsed)

    def _evaluate(self, agent_id, retries, cost, progress, elapsed):
        with self._lock:
            state = self.states.get(agent_id)
            retry_t = state.retry_threshold if state and state.retry_threshold is not None else self._retry_threshold
            cost_t  = state.cost_threshold  if state and state.cost_threshold  is not None else self._cost_threshold
            time_t  = state.time_threshold  if state and state.time_threshold  is not None else self._time_threshold

        # ── clear breach: all signals point to stuck ───────────────────────────
        clear_reasons = []
        if retries >= retry_t and progress == 0:
            clear_reasons.append(f"retried same action {retries}x with zero successful steps")
        if cost >= cost_t and progress == 0:
            clear_reasons.append(f"spent ${cost:.4f} with no progress")
        if elapsed > time_t and progress == 0:
            clear_reasons.append(f"exceeded {time_t:.0f}s with no progress")

        if clear_reasons:
            with self._lock:
                state = self.states.get(agent_id)
                if state and not state.alerted and not state.completed and not state.paused:
                    state.alerted = True
                    state.flagged = False  # escalate past flagged
                else:
                    return
            self._fire_alert(agent_id, "; ".join(clear_reasons), cost, retries, progress)
            return

        # ── ambiguous zone: some progress but signals are elevated ─────────────
        # Flag for human review instead of alerting outright.
        ambiguous_reasons = []
        if retries >= max(1, retry_t // 2) and progress > 0:
            ambiguous_reasons.append(f"{retries} retries despite some progress — may be stuck on a sub-task")
        if cost >= cost_t * 0.6 and progress > 0 and elapsed > time_t * 0.5:
            ambiguous_reasons.append(f"${cost:.4f} spent with only {progress} step(s) — cost/progress ratio is high")

        if ambiguous_reasons:
            with self._lock:
                state = self.states.get(agent_id)
                if state and not state.flagged and not state.alerted and not state.completed and not state.paused:
                    state.flagged = True
                    flag_reason = "; ".join(ambiguous_reasons)
                    state.notes.append(
                        f"[{datetime.datetime.now().strftime('%H:%M:%S')}] "
                        f"[watcher] needs review — {flag_reason}"
                    )
                else:
                    return
            self._dirty.set()
            print(f"\n[watcher] {agent_id} flagged for review — {'; '.join(ambiguous_reasons)}")

    def _fire_alert(self, agent_id, reason, cost, retries, progress):
        try:
            with self._lock:
                model = self.states[agent_id].model if agent_id in self.states else None
            situation = "zero_progress" if progress == 0 else "stuck_subtask"
            rec = _build_recommendation(agent_id, model, cost, retries, progress, situation)
        except Exception as e:
            print(f"[watcher] _fire_alert error building rec: {e}")
            rec = {"headline": "", "steps": [], "alternatives": []}
            model = None

        # pull similar past alerts from Redis memory (silent if unavailable)
        try:
            import memory as _mem
            similar = _mem.get_similar(agent_id, reason, model or "")
            rec["past_alerts"] = similar
            _mem.store_alert(agent_id, reason, model or "", cost, retries, progress)
        except Exception:
            rec["past_alerts"] = []

        alert = Alert(
            agent_id=agent_id,
            reason=reason,
            cost_usd=cost,
            retry_count=retries,
            progress_score=progress,
            recommendation=rec,
        )
        with self._lock:
            self.alerts.append(alert)
        self._dirty.set()
        self._on_alert(alert)

    def _print_alert(self, alert: Alert):
        rec = alert.recommendation
        print(f"\n{'='*60}")
        print(f"  ALERT — {alert.agent_id}")
        print(f"  Reason  : {alert.reason}")
        print(f"  {rec.get('headline', '')}")
        for i, step in enumerate(rec.get("steps", []), 1):
            print(f"  {i}. {step}")
        for a in rec.get("alternatives", []):
            print(f"     • {a['id']} — ${a['input_cost_per_1m']:.2f}/$1M in")
        print(f"{'='*60}\n")
