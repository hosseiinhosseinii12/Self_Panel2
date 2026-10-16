"""In-memory token bucket. Per-user (Telegram) and per-IP (Flask).

Includes background cleanup to prevent memory leak from unbounded keys.
"""
import threading
import time
from collections import defaultdict


class TokenBucket:
    def __init__(self, rate: int, per_seconds: float = 60.0,
                 cleanup_interval: float = 300.0, max_idle: float = 3600.0):
        self.rate = rate
        self.per = per_seconds
        self.tokens = defaultdict(lambda: float(rate))
        self.last = defaultdict(time.time)
        self.lock = threading.Lock()
        self.cleanup_interval = cleanup_interval
        self.max_idle = max_idle
        self._last_cleanup = time.time()

    def allow(self, key) -> bool:
        with self.lock:
            now = time.time()
            self._maybe_cleanup(now)
            elapsed = now - self.last[key]
            refill = elapsed * (self.rate / self.per)
            self.tokens[key] = min(self.rate, self.tokens[key] + refill)
            self.last[key] = now
            if self.tokens[key] >= 1:
                self.tokens[key] -= 1
                return True
            return False

    def _maybe_cleanup(self, now: float) -> None:
        if now - self._last_cleanup < self.cleanup_interval:
            return
        self._last_cleanup = now
        cutoff = now - self.max_idle
        stale = [k for k, t in self.last.items() if t < cutoff]
        for k in stale:
            self.tokens.pop(k, None)
            self.last.pop(k, None)


user_limiter = TokenBucket(rate=30, per_seconds=60.0)
ip_limiter = TokenBucket(rate=60, per_seconds=60.0)