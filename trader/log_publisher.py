"""Redis log publisher for streaming bot logs to UI."""

import logging
import json
from datetime import datetime
from typing import Any
import redis.asyncio as redis


class RedisLogHandler(logging.Handler):
    """Publish log records to Redis for real-time streaming."""

    def __init__(self, redis_client: redis.Redis, bot_key: str, max_logs: int = 500):
        super().__init__()
        self.redis = redis_client
        self.bot_key = bot_key  # e.g., "BTCUSDT:momshort"
        self.max_logs = max_logs
        self.channel = f"logs:{bot_key}"

    def emit(self, record: logging.LogRecord) -> None:
        """Publish log record to Redis channel and store in list."""
        try:
            # Format log entry
            log_entry = {
                "timestamp": datetime.now().isoformat(),
                "level": record.levelname,
                "message": self.format(record),
                "bot": self.bot_key,
            }

            # Publish to channel for real-time streaming
            self.redis.publish(self.channel, json.dumps(log_entry))

            # Store in list for history (keep last N logs)
            list_key = f"logs:history:{self.bot_key}"
            self.redis.lpush(list_key, json.dumps(log_entry))
            self.redis.ltrim(list_key, 0, self.max_logs - 1)

        except Exception:
            self.handleError(record)


async def get_log_history(redis_client: redis.Redis, bot_key: str, limit: int = 100) -> list[dict[str, Any]]:
    """Retrieve recent log history for a bot."""
    list_key = f"logs:history:{bot_key}"
    logs = await redis_client.lrange(list_key, 0, limit - 1)
    return [json.loads(log) for log in logs]
