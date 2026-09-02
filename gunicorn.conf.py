import os

bind = "unix:/var/www/gout-stopper/gout-stopper.sock"
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
chdir = "/var/www/gout-stopper"

# Local files under app/logs/ rather than journald (see standards §7). Paths
# resolve relative to chdir above.
accesslog = "app/logs/access.log"
errorlog = "app/logs/app.log"
# The app logs via logging.basicConfig -> stderr; without this, those lines
# (incl. the per-call "LLM ..." timing) land in journald instead of app.log.
capture_output = True
# Identical across apps so one tailing script sees the same columns everywhere:
# timestamp, client IP, request line, status, response bytes, duration.
access_log_format = '%(t)s %(h)s "%(r)s" %(s)s %(b)s %(L)ss'
# Read from the process env: this file runs before the app boots and can't
# import Settings.
loglevel = os.environ.get("LOG_LEVEL", "info")

# nginx proxies to this unix socket (see /etc/nginx/sites-enabled/lab.kudithipudi.org),
# so the peer connection has no IP at all — uvicorn's default trusted-proxy check
# (forwarded_allow_ips="127.0.0.1") never matches a unix-socket peer, so
# X-Forwarded-For/X-Real-IP from nginx would otherwise be silently ignored and
# every request would appear to come from the same unknown client (breaking
# anything that keys off request.client.host, e.g. per-IP rate limiting).
# Safe to always-trust here since the socket is only reachable by local nginx.
forwarded_allow_ips = "*"
