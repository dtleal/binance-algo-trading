"""In-memory registry of running bot states, readable by the API."""
import threading
from typing import Any

_lock = threading.Lock()
_states: dict[str, dict[str, Any]] = {}  # key: "{symbol}:{strategy}"


def update(key: str, patch: dict[str, Any]) -> None:
    with _lock:
        existing = _states.get(key, {})
        _states[key] = {**existing, **patch}


def get_states() -> dict[str, dict[str, Any]]:
    with _lock:
        return dict(_states)


def remove(key: str) -> None:
    with _lock:
        _states.pop(key, None)
