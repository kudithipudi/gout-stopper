"""Client IP extraction for per-IP rate limiting.

Storage lives in the rate_limit_hits SQLite table (see
app.db.check_and_record_rate_limit) rather than an in-process counter:
gunicorn workers don't share memory, so an in-process dict under-counts
whenever there's more than one worker, and even at one worker a restart
silently resets everyone's window.
"""

from fastapi import Request


def client_ip(request: Request) -> str:
    """Extract the real client IP from proxy headers.

    Gunicorn is bound to a unix socket behind nginx, so request.client is
    often empty and, when present, isn't the visitor. nginx sets
    X-Forwarded-For/X-Real-IP on every proxied location, so those take
    priority over the raw socket peer.
    """
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    return request.client.host if request.client else "unknown"
