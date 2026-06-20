"""
Peer-judge layer: a cheap secondary LLM call that checks whether an agent's
output is actually on-task — catching subtle drift that numeric signals miss.

Only triggers when signals are AMBIGUOUS: some progress but also high cost/retries.
Never runs on clearly healthy or clearly broken agents (waste of money).

Redis/RedisVL integration: every verdict is embedded and stored in a vector
index. Before calling the LLM, we retrieve the most similar past cases so the
judge isn't flying blind — it learns from prior runs.

Requires ANTHROPIC_API_KEY in environment.
Redis connection requires REDIS_HOST / REDIS_PORT / REDIS_USERNAME / REDIS_PASSWORD.
"""

import os
import json
import time
import hashlib
import numpy as np


_EMBED_DIM = 128


def _embed(text: str) -> list[float]:
    vec = np.zeros(_EMBED_DIM, dtype=np.float32)
    text = text.lower()
    for i in range(len(text) - 2):
        trigram = text[i:i + 3]
        h = int(hashlib.md5(trigram.encode()).hexdigest(), 16)
        vec[h % _EMBED_DIM] += 1.0
    norm = np.linalg.norm(vec)
    if norm > 0:
        vec /= norm
    return vec.tolist()


_INDEX_NAME = "selfaudit:judgments"
_PREFIX = "selfaudit:judgment:"

_SCHEMA = {
    "index": {
        "name": _INDEX_NAME,
        "prefix": _PREFIX,
        "storage_type": "json",
    },
    "fields": [
        {"name": "agent_id",         "type": "tag"},
        {"name": "on_task",          "type": "tag"},
        {"name": "confidence",       "type": "tag"},
        {"name": "reason",           "type": "text"},
        {"name": "task_description", "type": "text"},
        {"name": "timestamp",        "type": "numeric"},
        {
            "name": "embedding",
            "type": "vector",
            "attrs": {
                "algorithm": "flat",
                "dims": _EMBED_DIM,
                "distance_metric": "cosine",
                "datatype": "float32",
            },
        },
    ],
}


def _redis_client():
    try:
        import redis as redis_lib
        from dotenv import load_dotenv
        _here = os.path.dirname(os.path.abspath(__file__))
        load_dotenv(os.path.join(_here, ".env"))
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", 6379))
        use_ssl = host.endswith(".redis.io") or port != 6379
        r = redis_lib.Redis(
            host=host,
            port=port,
            username=os.getenv("REDIS_USERNAME"),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=False,
            ssl=use_ssl,
            ssl_cert_reqs=None,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
        r.ping()
        return r
    except Exception:
        return None


def _get_index():
    try:
        from redisvl.index import SearchIndex
        r = _redis_client()
        if r is None:
            return None
        idx = SearchIndex.from_dict(_SCHEMA, redis_client=r)
        try:
            idx.create(overwrite=False)
        except Exception:
            pass
        return idx
    except Exception:
        return None


def _store_verdict(idx, agent_id: str, task_description: str,
                   recent_output: str, verdict: dict) -> None:
    try:
        text = f"{task_description}\n{recent_output}"
        vec = _embed(text)
        key_suffix = hashlib.md5(f"{agent_id}{time.time()}".encode()).hexdigest()[:12]
        doc = {
            "agent_id": agent_id,
            "on_task": str(verdict.get("on_task", True)).lower(),
            "confidence": verdict.get("confidence", "low"),
            "reason": verdict.get("reason", ""),
            "task_description": task_description[:500],
            "timestamp": time.time(),
            "embedding": np.array(vec, dtype=np.float32).tobytes(),
        }
        idx.load([doc], keys=[f"{_PREFIX}{key_suffix}"])
    except Exception:
        pass


def _retrieve_similar(idx, task_description: str, recent_output: str,
                      top_k: int = 3) -> list[dict]:
    try:
        from redisvl.query import VectorQuery
        text = f"{task_description}\n{recent_output}"
        vec = np.array(_embed(text), dtype=np.float32).tobytes()
        query = VectorQuery(
            vector=vec,
            vector_field_name="embedding",
            return_fields=["agent_id", "on_task", "confidence",
                           "reason", "task_description", "timestamp"],
            num_results=top_k,
        )
        return idx.query(query)
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


def should_peer_judge(progress_score: int, max_retry_count: int,
                      cumulative_cost: float, cost_threshold: float) -> bool:
    has_some_progress = progress_score > 0
    elevated_cost = cumulative_cost > cost_threshold * 0.8
    elevated_retries = max_retry_count >= 2
    return has_some_progress and (elevated_cost or elevated_retries)


def judge(agent_id: str, task_description: str, recent_output: str,
          progress_score: int, cumulative_cost: float) -> dict:
    idx = _get_index()
    similar = _retrieve_similar(idx, task_description, recent_output) if idx else []
    memory_context = _format_similar(similar)

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

    if idx is not None:
        _store_verdict(idx, agent_id, task_description, recent_output, verdict)

    verdict["_similar_cases_found"] = len(similar)
    return verdict