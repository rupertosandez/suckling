"""
Simple in-memory time-based cache.

Not thread-safe (we run a single asyncio event loop, so it's fine).
Not persisted across restarts (intentional — keep it simple).
"""
import time
from typing import Any


class TTLCache:
    """A dict-like cache with per-entry expiration."""

    def __init__(self, default_ttl_seconds: float = 3600):
        self._store: dict[str, tuple[float, Any]] = {}
        self._default_ttl = default_ttl_seconds

    def get(self, key: str) -> Any | None:
        """Return the cached value, or None if missing or expired."""
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.time() >= expires_at:
            # Expired — clean up and return None
            del self._store[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: float | None = None) -> None:
        """Store a value with an optional custom TTL."""
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl
        self._store[key] = (time.time() + ttl, value)

    def invalidate(self, key: str) -> None:
        """Remove a specific key, if present."""
        self._store.pop(key, None)

    def clear(self) -> None:
        """Wipe the entire cache."""
        self._store.clear()

    def size(self) -> int:
        """Return the number of cached entries (including expired ones not yet purged)."""
        return len(self._store)


# Module-level singleton — one cache for the whole bot
_cache = TTLCache(default_ttl_seconds=6 * 3600)  # 6 hours


def get(key: str) -> Any | None:
    return _cache.get(key)


def set(key: str, value: Any, ttl_seconds: float | None = None) -> None:
    _cache.set(key, value, ttl_seconds)


def invalidate(key: str) -> None:
    _cache.invalidate(key)


def clear() -> None:
    _cache.clear()


def size() -> int:
    return _cache.size()