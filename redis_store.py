"""
Redis-backed shared state for SelfAudit.

Writes the full dashboard snapshot to Redis after every watcher event so
multiple machines can read the same live data. Also handles pub/sub so the
SSE stream wakes up on any machine when state changes anywhere.

Falls back silently if Redis is unavailable — local-only mode still works.
"""

import json
import os
import time

_SNAPSHOT_KEY = "selfaudit:snapshot"
_CHANNEL      = "selfaudit:updates"
_SNAPSHOT_TTL = 86400  # 24 hours


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
            ssl=False,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        r.ping()
        return r
    except Exception:
        return None


def push_snapshot(snapshot: dict) -> None:
    """Write the full dashboard snapshot to Redis and notify subscribers."""
    r = _client()
    if not r:
        return
    try:
        payload = json.dumps(snapshot)
        r.set(_SNAPSHOT_KEY, payload, ex=_SNAPSHOT_TTL)
        r.publish(_CHANNEL, str(time.time()))
    except Exception:
        pass


def get_snapshot():
    """Read the latest snapshot from Redis. Returns None if unavailable."""
    r = _client()
    if not r:
        return None
    try:
        raw = r.get(_SNAPSHOT_KEY)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def subscribe():
    """
    Generator that yields whenever any machine pushes a new snapshot.
    Used by the SSE endpoint on machines that aren't running agents.
    """
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
            ssl=False,
            socket_connect_timeout=2,
            socket_timeout=30,
        )
        ps = r.pubsub()
        ps.subscribe(_CHANNEL)
        for msg in ps.listen():
            if msg["type"] == "message":
                yield
    except Exception:
        return
