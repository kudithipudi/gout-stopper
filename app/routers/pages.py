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
