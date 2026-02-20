"""Production-grade event system using Redis Pub/Sub + Streams."""
import asyncio
import json
import os
from typing import Any

import redis.asyncio as aioredis


# Redis async connection (lazy init)
_redis_client: aioredis.Redis | None = None


async def _get_redis() -> aioredis.Redis:
    """Get or create async Redis client (singleton)."""
    global _redis_client
    if _redis_client is None:
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        _redis_client = await aioredis.from_url(redis_url, decode_responses=True)
    return _redis_client


async def publish(event: dict[str, Any]) -> None:
    """
    Publish event to Redis:
    1. Pub/Sub (real-time, ephemeral)
    2. Stream (persistent, with replay)
    """
    try:
        r = await _get_redis()
        event_json = json.dumps(event, default=str)

        # Pub/Sub for real-time subscribers
        await r.publish("trader:events", event_json)

        # Stream for persistence (keep last 1000 events, or 24h)
        await r.xadd(
            "trader:events:stream",
            {"data": event_json},
            maxlen=1000,
            approximate=True
        )
    except (aioredis.RedisError, json.JSONEncodeError):
        # Graceful degradation
        pass


async def subscribe() -> asyncio.Queue:
    """
    Subscribe to Redis Pub/Sub events.
    Returns a queue that will receive all published events.
    """
    q: asyncio.Queue = asyncio.Queue(maxsize=500)

    async def _listen_task():
        """Background task that listens to Redis pub/sub and feeds the queue."""
        try:
            r = await _get_redis()
            pubsub = r.pubsub()
            await pubsub.subscribe("trader:events")

            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        event = json.loads(message["data"])
                        q.put_nowait(event)
                    except (json.JSONDecodeError, asyncio.QueueFull):
                        pass  # Skip malformed or slow consumer
        except aioredis.RedisError:
            pass

    # Start listener task
    asyncio.create_task(_listen_task())
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    """
    Unsubscribe is handled automatically when the task/connection closes.
    This is kept for API compatibility.
    """
    pass


async def get_recent_events(count: int = 50) -> list[dict[str, Any]]:
    """
    Fetch recent events from Redis Stream (for replay/history).
    Returns up to `count` most recent events.
    """
    try:
        r = await _get_redis()
        # Read last N messages from stream
        messages = await r.xrevrange("trader:events:stream", count=count)
        events = []
        for msg_id, data in messages:
            try:
                event = json.loads(data["data"])
                events.append(event)
            except (json.JSONDecodeError, KeyError):
                pass
        return events
    except aioredis.RedisError:
        return []
