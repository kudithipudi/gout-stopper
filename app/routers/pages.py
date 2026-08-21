from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

from app.config import get_settings

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["prefix"] = get_settings().root_path


@router.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


@router.get("/about")
async def about(request: Request):
    return templates.TemplateResponse(request, "about.html", {})


@router.get("/offline")
async def offline(request: Request):
    return templates.TemplateResponse(request, "offline.html", {})


@router.get("/manifest.webmanifest")
async def manifest(request: Request):
    return templates.TemplateResponse(
        request, "manifest.webmanifest", {}, media_type="application/manifest+json"
    )


@router.get("/sw.js")
async def service_worker(request: Request):
    # Served from the app root (not /static) so its default scope covers the
    # whole prefix, e.g. https://.../gout-stopper/ — a scope under /static/js/
    # would only control static assets, not page navigations.
    return templates.TemplateResponse(request, "sw.js", {}, media_type="application/javascript")
