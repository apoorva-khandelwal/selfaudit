"""
Peer-judge layer: a cheap secondary LLM call that checks whether an agent's
output is actually on-task — catching subtle drift that numeric signals miss.

Only triggers when signals are AMBIGUOUS: some progress but also high cost/retries.
Never runs on clearly healthy or clearly broken agents (waste of money).

Redis integration: every verdict is embedded and stored as a Redis hash.
Before calling the LLM, we retrieve the most similar past cases via cosine
similarity so the judge learns from prior runs.

Requires ANTHROPIC_API_KEY in environment.
Redis connection requires REDIS_HOST / REDIS_PORT / REDIS_USERNAME / REDIS_PASSWORD.
"""

import os
import json
import time
import hashlib
import numpy as np


_EMBED_DIM = 128
_KEY_PREFIX = "selfaudit:judgment:"
_INDEX_KEY  = "selfaudit:judgment:index"


def _embed(text: str) -> np.ndarray:
    """Character-trigram hash embedding — fast, deterministic, no ML model needed."""
    vec = np.zeros(_EMBED_DIM, dtype=np.float32)
    text = text.lower()
    for i in range(len(text) - 2):
        h = int(hashlib.md5(text[i:i+3].encode()).hexdigest(), 16)
        vec[h % _EMBED_DIM] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec


def _redis_client():
    try:
        import redis as redis_lib
        from dotenv import load_dotenv
        _here = os.path.dirname(os.path.abspath(__file__))
        load_dotenv(os.path.join(_here, ".env"))
        r = redis_lib.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            username=os.getenv("REDIS_USERNAME"),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=False,
            ssl=False,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        r.ping()
        return r
    except Exception:
        return None


def _store_verdict(r, agent_id: str, task_description: str,
                   recent_output: str, verdict: dict) -> None:
    """Embed and store a judgment in Redis."""
    try:
        vec = _embed(f"{task_description}\n{recent_output}")
        key_suffix = hashlib.md5(f"{agent_id}{time.time()}".encode()).hexdigest()[:12]
        key = f"{_KEY_PREFIX}{key_suffix}"
        r.hset(key, mapping={
            "agent_id":         agent_id,
            "on_task":          str(verdict.get("on_task", True)).lower(),
            "confidence":       verdict.get("confidence", "low"),
            "reason":           verdict.get("reason", ""),
            "task_description": task_description[:500],
            "timestamp":        str(time.time()),
            "embedding":        vec.tobytes(),
        })
        # track all keys in a set so we can scan them
        r.sadd(_INDEX_KEY, key)
    except Exception:
        pass


def _retrieve_similar(r, task_description: str, recent_output: str,
                      top_k: int = 3) -> list[dict]:
    """Fetch all stored judgments and return top_k by cosine similarity."""
    try:
        query_vec = _embed(f"{task_description}\n{recent_output}")
        keys = r.smembers(_INDEX_KEY)
        scored = []
        for key in keys:
            raw = r.hgetall(key)
            if not raw or b"embedding" not in raw:
                continue
            stored_vec = np.frombuffer(raw[b"embedding"], dtype=np.float32)
            score = float(np.dot(query_vec, stored_vec))
            scored.append((score, {
                "agent_id":   raw.get(b"agent_id", b"").decode(),
                "on_task":    raw.get(b"on_task", b"").decode(),
                "confidence": raw.get(b"confidence", b"").decode(),
                "reason":     raw.get(b"reason", b"").decode(),
            }))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in scored[:top_k]]
    except Exception:
        return []


def _format_similar(similar: list[dict]) -> str:
    if not similar:
        return ""
    lines = ["## Similar past judgments (from memory)\n"]
    for i, r in enumerate(similar, 1):
        lines.append(
            f"{i}. agent={r.get('agent_id', 'unknown')} | "
            f"on_task={r.get('on_task', '?')} | "
            f"confidence={r.get('confidence', '?')} | "
            f"reason: {r.get('reason', '')}"
        )
    return "\n".join(lines)


# ── public API ─────────────────────────────────────────────────────────────────

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

    Before calling the LLM, queries Redis for similar past judgments so the
    judge has memory of prior cases. After the verdict, stores the result for
    future lookups.

    Returns:
        {"on_task": bool, "confidence": "high"|"medium"|"low",
         "reason": str, "recommendation": str}
    """
    # ── Redis memory: retrieve similar past cases ──────────────────────────────
    r = _redis_client()
    similar = _retrieve_similar(r, task_description, recent_output) if r else []
    memory_context = _format_similar(similar)

    # ── LLM call ──────────────────────────────────────────────────────────────
    try:
        import anthropic
        client = anthropic.Anthropic()
    except Exception:
        return {"on_task": True, "confidence": "low",
                "reason": "peer judge unavailable (anthropic not installed)",
                "recommendation": ""}

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"on_task": True, "confidence": "low",
                "reason": "peer judge skipped (no API key)",
                "recommendation": ""}

    memory_section = f"\n{memory_context}\n" if memory_context else ""

    prompt = f"""You are a peer-judge reviewing an AI agent's output for task alignment.
{memory_section}
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
If similar past judgments are shown above, use them as weak signal — they are analogous cases, not identical.

Respond with ONLY a JSON object (no markdown):
{{"on_task": true|false, "confidence": "high"|"medium"|"low", "reason": "one sentence", "recommendation": "one sentence"}}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        verdict = json.loads(response.content[0].text.strip())
    except Exception as e:
        verdict = {"on_task": True, "confidence": "low",
                   "reason": f"peer judge error: {e}",
                   "recommendation": ""}

    # ── Redis memory: store this verdict for future retrieval ──────────────────
    if r is not None:
        _store_verdict(r, agent_id, task_description, recent_output, verdict)

    verdict["_similar_cases_found"] = len(similar)
    return verdict
