"""
SelfAudit — example runner.

Usage:
    python3 main.py               # 4 agents (default)
    python3 main.py --agents 12   # stress test with 12 agents
    python3 main.py --no-phoenix  # skip Phoenix, just the dashboard
"""

import time
import random
import threading
import argparse
from sdk import Watcher


# ── agent behaviors ────────────────────────────────────────────────────────────

def run_healthy(watcher: Watcher, agent_id: str):
    actions = ["fetch_data", "parse_response", "summarize", "write_output"]
    for i, action in enumerate(actions):
        time.sleep(random.uniform(1.0, 2.0))
        with watcher.trace(agent_id, action) as t:
            t.success(cost_usd=round(random.uniform(0.002, 0.008), 4),
                      completed=(i == len(actions) - 1))


def run_stuck(watcher: Watcher, agent_id: str):
    for _ in range(20):
        time.sleep(random.uniform(0.8, 1.2))
        with watcher.trace(agent_id, "call_external_api") as t:
            t.fail(cost_usd=round(random.uniform(0.015, 0.025), 4))


def run_slow(watcher: Watcher, agent_id: str):
    actions = ["load_dataset", "preprocess", "run_inference", "validate", "format_output"]
    for i, action in enumerate(actions):
        time.sleep(random.uniform(1.8, 2.5))
        with watcher.trace(agent_id, action) as t:
            t.success(cost_usd=round(random.uniform(0.005, 0.012), 4),
                      completed=(i == len(actions) - 1))


def run_fast_fail(watcher: Watcher, agent_id: str):
    time.sleep(0.2)
    with watcher.trace(agent_id, "authenticate") as t:
        t.fail(cost_usd=round(random.uniform(0.001, 0.003), 4))


def run_intermittent(watcher: Watcher, agent_id: str):
    """Makes some progress then gets stuck — tests the ambiguous zone."""
    for action in ["connect", "fetch"]:
        time.sleep(1.0)
        with watcher.trace(agent_id, action) as t:
            t.success(cost_usd=round(random.uniform(0.003, 0.007), 4))
    for _ in range(15):
        time.sleep(1.0)
        with watcher.trace(agent_id, "process_chunk") as t:
            t.fail(cost_usd=round(random.uniform(0.010, 0.020), 4))


# ── orchestrator ───────────────────────────────────────────────────────────────

BEHAVIORS = [run_healthy, run_stuck, run_slow, run_fast_fail, run_intermittent]
BEHAVIOR_NAMES = ["healthy", "stuck", "slow", "fast-fail", "intermittent"]


def build_agent_plan(n: int):
    """
    For n agents, assign behaviors so there's always at least one stuck agent
    and a mix of others.
    """
    plan = []
    # first 4 are always the canonical set
    fixed = [
        (run_healthy,      "healthy"),
        (run_stuck,        "stuck"),
        (run_slow,         "slow"),
        (run_fast_fail,    "fast-fail"),
        (run_intermittent, "intermittent"),
    ]
    for fn, name in fixed[:min(n, 5)]:
        idx = len(plan) + 1
        plan.append((fn, f"agent-{idx} ({name})"))

    # extra agents get random behaviors
    for i in range(len(plan), n):
        fn = random.choice(BEHAVIORS)
        name = BEHAVIOR_NAMES[BEHAVIORS.index(fn)]
        plan.append((fn, f"agent-{i+1} ({name})"))

    return plan


def main():
    parser = argparse.ArgumentParser(description="SelfAudit demo runner")
    parser.add_argument("--agents",     type=int, default=4,
                        help="number of agents to simulate (default: 4)")
    parser.add_argument("--no-phoenix", action="store_true",
                        help="skip Phoenix trace UI")
    args = parser.parse_args()

    if not args.no_phoenix:
        try:
            import tracing
            tracing.setup()
        except Exception as e:
            print(f"  Phoenix skipped ({e})\n")

    watcher = Watcher(
        task_description="Process a dataset and produce a structured summary report.",
    )
    watcher.start_dashboard(port=5050)

    plan = build_agent_plan(args.agents)
    print(f"SelfAudit — watching {len(plan)} agents\n")

    threads = [
        threading.Thread(target=fn, args=(watcher, agent_id), daemon=True)
        for fn, agent_id in plan
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    watcher.summary()
    print("Dashboard → http://localhost:5050  |  Ctrl+C to exit")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
