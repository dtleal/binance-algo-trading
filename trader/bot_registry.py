"""Shared registry of running bot states (Redis-based for multi-process, production-grade)."""
import asyncio
import json
import os
import time
from typing import Any

import redis


# Redis connection (lazy init)
_redis_client: redis.Redis | None = None
REGISTRY_TTL_SEC = int(os.getenv("BOT_REGISTRY_TTL_SEC", "7200"))
HEARTBEAT_INTERVAL_SEC = int(os.getenv("BOT_HEARTBEAT_INTERVAL_SEC", "10"))


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
        updated["heartbeat_ts"] = int(time.time())
        # Store back
        r.hset("bot:states", key, json.dumps(updated, default=str))
        # Keep hash alive as long as at least one bot is publishing state.
        r.expire("bot:states", REGISTRY_TTL_SEC)
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


async def heartbeat_loop(
    key: str,
    base_patch: dict[str, Any],
    interval_sec: int = HEARTBEAT_INTERVAL_SEC,
) -> None:
    """Publish lightweight heartbeat updates for long candle intervals."""
    while True:
        update(key, base_patch)
        await asyncio.sleep(interval_sec)
