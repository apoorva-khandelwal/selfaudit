"""
Redis-backed shared state for SelfAudit.

Data model:
  selfaudit:agents          SET   — all agent IDs active this session
  selfaudit:agent:{id}      HASH  — per-agent state (cost, retries, status, …)
  selfaudit:alerts          ZSET  — alert log, scored by insertion order
  selfaudit:flagged         ZSET  — flagged-for-review entries, scored by insertion order
  selfaudit:stats           HASH  — session-level totals (cost, counts)
  selfaudit:updates         channel — pub/sub for live SSE notifications

Falls back silently if Redis is unavailable — local-only mode still works.
"""

import json
import os
import time

_NS      = "selfaudit"
_CHANNEL = f"{_NS}:updates"
_TTL     = 86400  # 24 hours

_KEY_AGENTS  = f"{_NS}:agents"
_KEY_AGENT   = f"{_NS}:agent:"   # + agent_id
_KEY_ALERTS  = f"{_NS}:alerts"
_KEY_FLAGGED = f"{_NS}:flagged"
_KEY_STATS   = f"{_NS}:stats"


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
    """
    Write dashboard state to Redis using typed data structures.
    Each agent gets its own HASH; alerts and flagged entries live in ZSETs.
    """
    r = _client()
    if not r:
        return
    try:
        pipe = r.pipeline()

        # session-level counters
        pipe.hset(_KEY_STATS, mapping={
            "total_cost":    snapshot["total_cost"],
            "alert_count":   snapshot["alert_count"],
            "done_count":    snapshot["done_count"],
            "running_count": snapshot["running_count"],
            "paused_count":  snapshot["paused_count"],
        })
        pipe.expire(_KEY_STATS, _TTL)

        # one HASH per agent
        for agent in snapshot["agents"]:
            aid = agent["agent_id"]
            pipe.sadd(_KEY_AGENTS, aid)
            pipe.hset(_KEY_AGENT + aid, mapping={
                "agent_id":       aid,
                "status":         agent["status"],
                "cost":           agent["cost"],
                "progress":       agent["progress"],
                "retries":        agent["retries"],
                "elapsed":        agent["elapsed"],
                "alerted":        int(agent["alerted"]),
                "paused":         int(agent["paused"]),
                "flagged":        int(agent["flagged"]),
                "alert_reason":   agent["alert_reason"] or "",
                "proj_1h":        agent["proj_1h"],
                "budget":         agent["budget"] or "",
                "model":          agent["model"] or "",
                "progress_mode":  agent["progress_mode"],
                "t_retry":        agent["t_retry"] if agent["t_retry"] is not None else "",
                "t_cost":         agent["t_cost"] if agent["t_cost"] is not None else "",
                "t_time":         agent["t_time"] if agent["t_time"] is not None else "",
                "notes":          json.dumps(agent["notes"]),
                "recent_actions": json.dumps(agent["recent_actions"]),
            })
            pipe.expire(_KEY_AGENT + aid, _TTL)
        pipe.expire(_KEY_AGENTS, _TTL)

        # alert log as a ZSET (score = insertion index, preserves order)
        pipe.delete(_KEY_ALERTS)
        for i, alert in enumerate(snapshot["alerts"]):
            pipe.zadd(_KEY_ALERTS, {json.dumps(alert): i})
        if snapshot["alerts"]:
            pipe.expire(_KEY_ALERTS, _TTL)

        # flagged-for-review as a ZSET
        pipe.delete(_KEY_FLAGGED)
        for i, entry in enumerate(snapshot["flagged"]):
            pipe.zadd(_KEY_FLAGGED, {json.dumps(entry): i})
        if snapshot["flagged"]:
            pipe.expire(_KEY_FLAGGED, _TTL)

        pipe.execute()
        r.publish(_CHANNEL, str(time.time()))
    except Exception:
        pass


def get_snapshot():
    """
    Reconstruct the dashboard snapshot from individual Redis keys.
    Returns None if Redis is unavailable or no data exists yet.
    """
    r = _client()
    if not r:
        return None
    try:
        stats_raw = r.hgetall(_KEY_STATS)
        if not stats_raw:
            return None

        def s(v):
            return v.decode() if isinstance(v, bytes) else v

        stats = {s(k): s(v) for k, v in stats_raw.items()}

        # rebuild agent list from individual hashes
        agents = []
        for aid_bytes in r.smembers(_KEY_AGENTS):
            aid = aid_bytes.decode()
            raw = r.hgetall(_KEY_AGENT + aid)
            if not raw:
                continue
            h = {k.decode(): v.decode() for k, v in raw.items()}
            agents.append({
                "agent_id":       h["agent_id"],
                "status":         h["status"],
                "cost":           h["cost"],
                "progress":       int(h["progress"]),
                "retries":        int(h["retries"]),
                "elapsed":        h["elapsed"],
                "alerted":        bool(int(h["alerted"])),
                "paused":         bool(int(h["paused"])),
                "flagged":        bool(int(h["flagged"])),
                "alert_reason":   h["alert_reason"] or None,
                "proj_1h":        h["proj_1h"],
                "budget":         h["budget"] or None,
                "model":          h["model"] or None,
                "progress_mode":  h["progress_mode"],
                "t_retry":        float(h["t_retry"]) if h["t_retry"] else None,
                "t_cost":         float(h["t_cost"]) if h["t_cost"] else None,
                "t_time":         float(h["t_time"]) if h["t_time"] else None,
                "notes":          json.loads(h["notes"]),
                "recent_actions": json.loads(h["recent_actions"]),
            })
        agents.sort(key=lambda a: a["agent_id"])

        # alerts from ZSET (ascending score = insertion order)
        alerts = [json.loads(v) for v in r.zrange(_KEY_ALERTS, 0, -1)]

        # flagged entries from ZSET
        flagged = [json.loads(v) for v in r.zrange(_KEY_FLAGGED, 0, -1)]

        return {
            "agents":        agents,
            "alerts":        alerts,
            "flagged":       flagged,
            "total_cost":    stats.get("total_cost", "0.0000"),
            "alert_count":   int(stats.get("alert_count", 0)),
            "done_count":    int(stats.get("done_count", 0)),
            "running_count": int(stats.get("running_count", 0)),
            "paused_count":  int(stats.get("paused_count", 0)),
            "cost_saved":    "0.00",
        }
    except Exception:
        return None


def subscribe():
    """
    Generator that yields whenever any machine pushes a new snapshot.
    Used by the SSE endpoint on viewer machines.
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