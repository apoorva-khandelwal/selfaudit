"""
Peer-judge layer: a cheap secondary LLM call that checks whether an agent's
output is actually on-task — catching subtle drift that numeric signals miss.

Only triggers when signals are AMBIGUOUS: some progress but also high cost/retries.
Never runs on clearly healthy or clearly broken agents (waste of money).

Requires ANTHROPIC_API_KEY in environment.
"""

import os
import json


def _client():
    try:
        import anthropic
        return anthropic.Anthropic()
    except Exception:
        return None


def should_peer_judge(progress_score: int, max_retry_count: int,
                      cumulative_cost: float, cost_threshold: float) -> bool:
    """
    Only invoke the peer judge in the ambiguous middle zone:
    - Agent has made SOME progress (not clearly broken)
    - But cost or retries are elevated (not clearly healthy)
    """
    has_some_progress = progress_score > 0
    elevated_cost = cumulative_cost > cost_threshold * 0.8
    elevated_retries = max_retry_count >= 2
    return has_some_progress and (elevated_cost or elevated_retries)


def judge(agent_id: str, task_description: str, recent_output: str,
          progress_score: int, cumulative_cost: float) -> dict:
    """
    Ask a cheap LLM to read the agent's actual output and decide if it's on-task.

    Returns:
        {"on_task": bool, "confidence": "high"|"medium"|"low",
         "reason": str, "recommendation": str}
    """
    client = _client()
    if client is None:
        return {"on_task": True, "confidence": "low",
                "reason": "peer judge unavailable (anthropic not installed)",
                "recommendation": ""}

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"on_task": True, "confidence": "low",
                "reason": "peer judge skipped (no API key)",
                "recommendation": ""}

    prompt = f"""You are a peer-judge reviewing an AI agent's output for task alignment.

## Task the agent was given
{task_description}

## Agent's most recent output
{recent_output}

## Context
- Agent: {agent_id}
- Progress steps completed: {progress_score}
- Cumulative cost: ${cumulative_cost:.4f}

## Your job
Decide if the agent is still working on the right task, or if it has drifted off-task.
Drift examples: answering a different question, producing irrelevant output, repeating itself without progress.

Respond with ONLY a JSON object (no markdown):
{{"on_task": true|false, "confidence": "high"|"medium"|"low", "reason": "one sentence", "recommendation": "one sentence"}}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}],
        )
        return json.loads(response.content[0].text.strip())
    except Exception as e:
        return {"on_task": True, "confidence": "low",
                "reason": f"peer judge error: {e}",
                "recommendation": ""}
