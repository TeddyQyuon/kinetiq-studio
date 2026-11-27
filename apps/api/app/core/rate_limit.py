from __future__ import annotations

import math
import secrets
import threading
import time
from collections import OrderedDict, deque
from functools import lru_cache

from fastapi import HTTPException
from redis import Redis, RedisError

from app.core.config import get_settings


class SlidingWindow:
    def __init__(self):
        self.hits: OrderedDict[str, deque[float]] = OrderedDict()
        self.lock = threading.Lock()

    def retry_after(self, key: str, limit: int, window: int) -> int:
        with self.lock:
            now = time.monotonic()
            cutoff = now - window
            while self.hits and next(iter(self.hits.values()))[-1] <= cutoff:
                self.hits.popitem(last=False)
            hits = self.hits.setdefault(key, deque())
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                return max(1, math.ceil(hits[0] + window - now))
            hits.append(now)
            self.hits.move_to_end(key)
            return 0


_local = SlidingWindow()

# Use Redis time and one atomic operation across all API processes.
_WINDOW = """
local t = redis.call('TIME')
local now = t[1] * 1000000 + t[2]
local window = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window * 1000000)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
    local first = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    return math.max(1, math.ceil(window - (now - first[2]) / 1000000))
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('EXPIRE', KEYS[1], window)
return 0
"""


@lru_cache(maxsize=1)
def _redis(url):
    return Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)


def check_worker_rate(key: str):
    cfg = get_settings()
    if cfg.redis_url:
        try:
            retry = _redis(cfg.redis_url).eval(
                _WINDOW,
                1,
                "kinetiq:rate:" + key,
                cfg.worker_rate_limit,
                cfg.worker_rate_window_seconds,
                secrets.token_hex(12),
            )
        except RedisError:
            # Bound requests even while the shared store is unavailable.
            retry = _local.retry_after(key, cfg.worker_rate_limit, cfg.worker_rate_window_seconds)
    else:
        retry = _local.retry_after(key, cfg.worker_rate_limit, cfg.worker_rate_window_seconds)
    if retry:
        raise HTTPException(429, "Worker rate limit exceeded", headers={"Retry-After": str(retry)})
