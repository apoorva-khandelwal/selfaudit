"""
Simulated worker agents. Each agent runs a task and emits trace events.
Also sends OpenTelemetry spans to Phoenix when tracing is set up.

Behaviors:
  - Agent 1: healthy, completes successfully
  - Agent 2: stuck in retry loop (the $47K scenario)
  - Agent 3: slow but making progress
  - Agent 4: fails fast, gives up cleanly
"""

import time
import random
from dataclasses import dataclass
from typing import Callable
from opentelemetry import trace


@dataclass
class TraceEvent:
    agent_id: str
    timestamp: float
    action: str
    cost_usd: float
    success: bool
    cumulative_cost_usd: float
    retry_count: int
    completed: bool = False


def _emit(handler: Callable[[TraceEvent], None], event: TraceEvent):
    tracer = trace.get_tracer("selfaudit")
    with tracer.start_as_current_span(event.action) as span:
        span.set_attribute("agent.id", event.agent_id)
        span.set_attribute("agent.action", event.action)
        span.set_attribute("agent.success", event.success)
        span.set_attribute("agent.cost_usd", event.cost_usd)
        span.set_attribute("agent.cumulative_cost_usd", event.cumulative_cost_usd)
        span.set_attribute("agent.retry_count", event.retry_count)
        span.set_attribute("agent.completed", event.completed)
        if not event.success:
            span.set_status(trace.StatusCode.ERROR, f"action failed: {event.action}")
    handler(event)


def run_agent_1(handler: Callable[[TraceEvent], None]):
    """Healthy agent — does work, succeeds, stops."""
    agent_id = "agent-1"
    cumulative = 0.0
    actions = ["fetch_data", "parse_response", "summarize", "write_output"]
    tracer = trace.get_tracer("selfaudit")
    with tracer.start_as_current_span("agent-1-task"):
        for i, action in enumerate(actions):
            time.sleep(1.5)
            cost = round(random.uniform(0.002, 0.008), 4)
            cumulative += cost
            _emit(handler, TraceEvent(
                agent_id=agent_id,
                timestamp=time.time(),
                action=action,
                cost_usd=cost,
                success=True,
                cumulative_cost_usd=round(cumulative, 4),
                retry_count=0,
                completed=(i == len(actions) - 1),
            ))


def run_agent_2(handler: Callable[[TraceEvent], None]):
    """Stuck agent — retries the same failing action forever."""
    agent_id = "agent-2"
    cumulative = 0.0
    retry_count = 0
    tracer = trace.get_tracer("selfaudit")
    with tracer.start_as_current_span("agent-2-task"):
        for _ in range(20):
            time.sleep(1.0)
            cost = round(random.uniform(0.015, 0.025), 4)
            cumulative += cost
            retry_count += 1
            _emit(handler, TraceEvent(
                agent_id=agent_id,
                timestamp=time.time(),
                action="call_external_api",
                cost_usd=cost,
                success=False,
                cumulative_cost_usd=round(cumulative, 4),
                retry_count=retry_count,
                completed=False,
            ))


def run_agent_3(handler: Callable[[TraceEvent], None]):
    """Slow agent — making real progress, just slowly."""
    agent_id = "agent-3"
    cumulative = 0.0
    actions = ["load_dataset", "preprocess", "run_inference", "validate", "format_output"]
    tracer = trace.get_tracer("selfaudit")
    with tracer.start_as_current_span("agent-3-task"):
        for i, action in enumerate(actions):
            time.sleep(2.0)
            cost = round(random.uniform(0.005, 0.012), 4)
            cumulative += cost
            _emit(handler, TraceEvent(
                agent_id=agent_id,
                timestamp=time.time(),
                action=action,
                cost_usd=cost,
                success=True,
                cumulative_cost_usd=round(cumulative, 4),
                retry_count=0,
                completed=(i == len(actions) - 1),
            ))


def run_agent_4(handler: Callable[[TraceEvent], None]):
    """Fast-fail agent — tries once, fails, stops cleanly."""
    agent_id = "agent-4"
    tracer = trace.get_tracer("selfaudit")
    with tracer.start_as_current_span("agent-4-task"):
        time.sleep(0.1)
        cost = round(random.uniform(0.001, 0.003), 4)
        _emit(handler, TraceEvent(
            agent_id=agent_id,
            timestamp=time.time(),
            action="authenticate",
            cost_usd=cost,
            success=False,
            cumulative_cost_usd=cost,
            retry_count=1,
            completed=False,
        ))
