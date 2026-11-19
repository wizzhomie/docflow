"""A tiny per-client rate limiter for the public chat surface.

The assistant is a demo behind a public URL: the model runs on two CPU cores and one
question can occupy it for a minute. Without a limit a single visitor can keep the widget
unusable for everyone else, so the HTTP layer counts requests per client in a sliding
window before it reaches the model.

Deliberately small: a dict of timestamps per key, pruned on every call and capped by the
number of keys, so a forgotten key cannot grow forever.
"""
from __future__ import annotations

import threading
import time


class SlidingWindow:
    """Allow at most `limit` events per `window` seconds for each key."""

    def __init__(self, limit: int, window: float, max_keys: int = 2048):
        self.limit = max(1, int(limit))
        self.window = float(window)
        self.max_keys = max(1, int(max_keys))
        self._seen: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        moment = time.monotonic() if now is None else now
        cutoff = moment - self.window
        with self._lock:
            stamps = [stamp for stamp in self._seen.get(key, []) if stamp > cutoff]
            if len(stamps) >= self.limit:
                self._seen[key] = stamps
                return False
            stamps.append(moment)
            self._seen[key] = stamps
            self._prune(cutoff)
            return True

    def retry_after(self, key: str, now: float | None = None) -> int:
        """Seconds until the oldest counted request leaves the window."""
        moment = time.monotonic() if now is None else now
        with self._lock:
            stamps = [stamp for stamp in self._seen.get(key, []) if stamp > moment - self.window]
            if len(stamps) < self.limit:
                return 0
            return max(1, int(round(self.window - (moment - stamps[0]))))

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()

    def _prune(self, cutoff: float) -> None:
        if len(self._seen) <= self.max_keys:
            return
        for key in list(self._seen):
            if not self._seen[key] or self._seen[key][-1] <= cutoff:
                del self._seen[key]
        while len(self._seen) > self.max_keys:
            self._seen.pop(next(iter(self._seen)))
