bind = "unix:/var/www/gout-stopper/gout-stopper.sock"
workers = 1
worker_class = "uvicorn.workers.UvicornWorker"
chdir = "/var/www/gout-stopper"
accesslog = "-"
errorlog = "-"

# nginx proxies to this unix socket (see /etc/nginx/sites-enabled/lab.kudithipudi.org),
# so the peer connection has no IP at all — uvicorn's default trusted-proxy check
# (forwarded_allow_ips="127.0.0.1") never matches a unix-socket peer, so
# X-Forwarded-For/X-Real-IP from nginx would otherwise be silently ignored and
# every request would appear to come from the same unknown client (breaking
# anything that keys off request.client.host, e.g. per-IP rate limiting).
# Safe to always-trust here since the socket is only reachable by local nginx.
forwarded_allow_ips = "*"
