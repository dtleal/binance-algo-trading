"""Shared registry of running bot states (Redis-based for multi-process, production-grade)."""
import json
import os
from typing import Any

import redis


# Redis connection (lazy init)
_redis_client: redis.Redis | None = None


def _get_redis() -> redis.Redis:
    """Get or create Redis client (singleton)."""
    global _redis_client
    if _redis_client is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        _redis_client = redis.from_url(redis_url, decode_responses=True)
    return _redis_client


def update(key: str, patch: dict[str, Any]) -> None:
    """Update bot state (merges patch into existing state)."""
    try:
        r = _get_redis()
        # Get existing state
        existing_json = r.hget("bot:states", key)
        existing = json.loads(existing_json) if existing_json else {}
        # Merge patch
        updated = {**existing, **patch}
        # Store back
        r.hset("bot:states", key, json.dumps(updated, default=str))
        # Set TTL on the hash (auto-cleanup stale bots after 5 minutes of no updates)
        r.expire("bot:states", 300)
    except (redis.RedisError, json.JSONDecodeError):
        # Graceful degradation - if Redis is down, silently fail
        pass


def get_states() -> dict[str, dict[str, Any]]:
    """Get all bot states."""
    try:
        r = _get_redis()
        states = r.hgetall("bot:states")
        return {k: json.loads(v) for k, v in states.items()}
    except (redis.RedisError, json.JSONDecodeError):
        return {}


def remove(key: str) -> None:
    """Remove a bot from the registry."""
    try:
        r = _get_redis()
        r.hdel("bot:states", key)
    except redis.RedisError:
        pass
