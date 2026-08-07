import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.db import init_db
from app.routers import admin, pages, scan

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    Path(get_settings().uploads_dir).mkdir(parents=True, exist_ok=True)
    logger.info("Startup complete")
    yield


settings = get_settings()
# Ensure the writable dirs exist before the /uploads mount, which requires a
# live directory at import time.
Path(settings.db_path).parent.mkdir(parents=True, exist_ok=True)
_uploads_abs = Path(settings.uploads_dir).resolve()
_uploads_abs.mkdir(parents=True, exist_ok=True)

# NOTE: don't pass root_path to FastAPI. nginx strips the /gout-stopper prefix
# before forwarding, so the app sees bare paths like /static/css/app.css. Setting
# FastAPI root_path makes Starlette's Mount routing expect the prefix to still be
# present, and the /static mount 404s. Templates still get the public prefix via
# the ROOT_PATH env -> {{ prefix }} global.
app = FastAPI(lifespan=lifespan)
_here = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=str(_here / "static")), name="static")
app.mount("/uploads", StaticFiles(directory=str(_uploads_abs)), name="uploads")
# Signs the admin login cookie. Falls back to admin_password so a fresh
# checkout still runs, but a real deploy should set SESSION_SECRET in .env —
# without a stable secret, every gunicorn restart would log everyone out.
#
# Deliberately left at the default cookie path "/" rather than root_path:
# nginx strips /gout-stopper before proxying, so the app (and tests, which
# talk to it directly) only ever sees bare paths like "/admin" — a cookie
# scoped to "/gout-stopper" would never match those and the session would
# silently never be sent back.
_session_secret = settings.session_secret or settings.admin_password
if not _session_secret:
    raise RuntimeError(
        "SESSION_SECRET or ADMIN_PASSWORD must be set — refusing to start with an "
        "insecure default session secret"
    )
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret,
    session_cookie="gout_stopper_session",
    max_age=12 * 60 * 60,
)
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["prefix"] = settings.root_path

app.include_router(pages.router)
app.include_router(scan.router)
app.include_router(admin.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if request.url.path.startswith("/admin"):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
