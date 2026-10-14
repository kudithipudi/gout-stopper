"""Content-hashed static URLs for templates (standards §5).

Starlette's StaticFiles sends no Cache-Control header, so Cloudflare caches
/static/* at the edge for its default TTL (up to 2 hours). After a deploy,
visitors get the new HTML with the old JS/CSS. `asset("js/app.js")` returns
`<base>/js/app.js?v=<10 hex chars of the file's SHA-256>`, so a changed file
gets a new URL straight away and an unchanged one keeps its cached copy.

    from app.static_assets import install_asset_global
    install_asset_global(templates, f"{settings.root_path}/static", "app/static")

    <script src="{{ asset('js/app.js') }}"></script>

`templates` may be a Jinja2Templates or a jinja2 Environment (Flask:
`app.jinja_env`). Call it for every templates object the app creates.
This file is identical across lab apps; copy it, don't fork it.
"""

import functools
import hashlib
from pathlib import Path


@functools.lru_cache(maxsize=512)
def _digest(path: str, mtime_ns: int, size: int) -> str:
    # mtime and size are part of the key, so an edited file is re-hashed.
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()[:10]


def install_asset_global(templates, base: str, static_dir: str | Path) -> None:
    root = Path(static_dir).resolve()
    base = base.rstrip("/")

    def asset(path: str) -> str:
        file = root / path
        try:
            st = file.stat()
        except OSError:
            return f"{base}/{path}"
        return f"{base}/{path}?v={_digest(str(file), st.st_mtime_ns, st.st_size)}"

    env = getattr(templates, "env", templates)
    env.globals["asset"] = asset
