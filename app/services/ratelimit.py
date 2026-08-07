"""Tiny in-process rate limiter.

This app runs as a single gunicorn worker with no Redis/external infra, so a
plain module-level dict is sufficient — state just needs to survive across
requests within this one process, not across restarts or workers.

Sliding-window-by-log: for each key (client IP) we keep a list of the
monotonic timestamps of recent requests and drop anything older than the
window before checking/appending. Good enough for a handful of requests per
minute; not meant to scale beyond that.
"""

import time
from collections import defaultdict

WINDOW_SECONDS = 60.0

# key -> list of request timestamps (time.monotonic()) within the last window.
_hits: dict[str, list[float]] = defaultdict(list)


def reset() -> None:
    """Clear all rate-limit state. Used by tests to get an isolated start."""
    _hits.clear()


def check(key: str, limit: int, *, now: float | None = None) -> bool:
    """Record a hit for `key` and return True if it's within `limit` per
    WINDOW_SECONDS, or False if the caller should be rate-limited."""
    now = time.monotonic() if now is None else now
    cutoff = now - WINDOW_SECONDS
    hits = [t for t in _hits[key] if t > cutoff]
    if len(hits) >= limit:
        _hits[key] = hits
        return False
    hits.append(now)
    _hits[key] = hits
    return True
