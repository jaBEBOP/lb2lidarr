"""
cache.py — TTLCache and RateLimiter shared across all modules.
"""

import time
from threading import Lock


class TTLCache:
    """Thread-safe in-memory cache with a per-entry time-to-live."""

    def __init__(self, ttl: int = 3600):
        self._cache: dict = {}
        self._ttl = ttl
        self._lock = Lock()

    def get(self, key):
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            value, ts = entry
            if time.time() - ts < self._ttl:
                return value
            del self._cache[key]
        return None

    def set(self, key, value) -> None:
        with self._lock:
            self._cache[key] = (value, time.time())

    def delete(self, key) -> None:
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def get_or_set(self, key, func, *args, **kwargs):
        """Return cached value, or call func(*args) to compute and cache it."""
        cached = self.get(key)
        if cached is not None:
            return cached
        value = func(*args, **kwargs)
        if value is not None:
            self.set(key, value)
        return value


class RateLimiter:
    """Enforces a minimum gap between calls when enabled."""

    def __init__(self, enabled: bool = True, delay: float = 0.5):
        self.enabled = enabled
        self.delay = delay
        self._last_call: float = 0.0
        self._lock = Lock()

    def wait(self) -> None:
        if not self.enabled:
            return
        with self._lock:
            elapsed = time.time() - self._last_call
            if elapsed < self.delay:
                time.sleep(self.delay - elapsed)
            self._last_call = time.time()
