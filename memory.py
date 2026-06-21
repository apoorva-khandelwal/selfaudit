"""
Alert memory layer — stores past alerts in Redis and retrieves similar ones
when a new alert fires. Gives devs context: "this happened before, here's what occurred."

Falls back silently if Redis is unavailable — memory is a nice-to-have, not required.

Requires REDIS_HOST / REDIS_PORT / REDIS_USERNAME / REDIS_PASSWORD in environment (or .env).
"""

import os
import time
import hashlib
import numpy as np

_EMBED_DIM  = 128
_KEY_PREFIX = "selfaudit:alert:"
_INDEX_KEY  = "selfaudit:alert:index"


def _embed(text: str) -> np.ndarray:
    """Character-trigram hash embedding — fast, deterministic, no ML model needed."""
    vec = np.zeros(_EMBED_DIM, dtype=np.float32)
    text = text.lower()
    for i in range(len(text) - 2):
        h = int(hashlib.md5(text[i:i+3].encode()).hexdigest(), 16)
        vec[h % _EMBED_DIM] += 1.0
    norm = np.linalg.norm(vec)
    return vec / norm if norm > 0 else vec


def _client():
    try:
        import redis as redis_lib
        try:
            from dotenv import load_dotenv
            load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
        except ImportError:
            pass
        r = redis_lib.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            username=os.getenv("REDIS_USERNAME"),
            password=os.getenv("REDIS_PASSWORD"),
            decode_responses=False,
            ssl=False,
            socket_connect_timeout=3,
            socket_timeout=3,
        )
        r.ping()
        return r
    except Exception:
        return None


def store_alert(agent_id: str, reason: str, model: str, cost: float,
                retries: int, progress: int, outcome: str = "open") -> None:
    """Store an alert in Redis for future similarity lookup."""
    r = _client()
    if not r:
        return
    try:
        vec = _embed(f"{agent_id} {reason} {model or ''}")
        suffix = hashlib.md5(f"{agent_id}{time.time()}".encode()).hexdigest()[:12]
        key = f"{_KEY_PREFIX}{suffix}"
        r.hset(key, mapping={
            "agent_id": agent_id,
            "reason":   reason,
            "model":    model or "",
            "cost":     str(round(cost, 4)),
            "retries":  str(retries),
            "progress": str(progress),
            "outcome":  outcome,
            "ts":       str(time.time()),
            "embedding": vec.tobytes(),
        })
        r.sadd(_INDEX_KEY, key)
    except Exception:
        pass


def get_similar(agent_id: str, reason: str, model: str, top_k: int = 3,
                min_age_seconds: float = 3600) -> list:
    """Return top_k past alerts most similar to this one, from previous sessions only."""
    r = _client()
    if not r:
        return []
    try:
        import datetime
        query_vec = _embed(f"{agent_id} {reason} {model or ''}")
        keys = r.smembers(_INDEX_KEY)
        cutoff = time.time() - min_age_seconds
        scored = []
        for key in keys:
            raw = r.hgetall(key)
            if not raw or b"embedding" not in raw:
                continue
            ts = float(raw.get(b"ts", b"0").decode())
            if ts > cutoff:
                continue  # skip alerts from this session
            stored = np.frombuffer(raw[b"embedding"], dtype=np.float32)
            score = float(np.dot(query_vec, stored))
            scored.append((score, {
                "agent_id": raw.get(b"agent_id", b"").decode(),
                "reason":   raw.get(b"reason", b"").decode(),
                "model":    raw.get(b"model", b"").decode(),
                "cost":     raw.get(b"cost", b"0").decode(),
                "outcome":  raw.get(b"outcome", b"open").decode(),
                "time":     datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "?",
            }))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _, d in scored[:top_k]]
    except Exception:
        return []
