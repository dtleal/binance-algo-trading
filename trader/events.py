"""Shared async pub/sub event bus — bots publish, API WebSocket subscribers consume."""
import asyncio
from typing import Any

_subscribers: list[asyncio.Queue] = []


async def publish(event: dict[str, Any]) -> None:
    """Broadcast an event to every subscribed WebSocket queue (fire-and-forget)."""
    for q in list(_subscribers):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass  # slow consumer — drop rather than block


def subscribe() -> asyncio.Queue:
    """Register a new consumer. Returns its dedicated queue."""
    q: asyncio.Queue = asyncio.Queue(maxsize=500)
    _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    try:
        _subscribers.remove(q)
    except ValueError:
        pass
