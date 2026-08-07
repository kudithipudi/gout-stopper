import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import get_db

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["prefix"] = get_settings().root_path

_CATEGORIES = {"avoid", "limit", "ok"}


def _is_admin(request: Request) -> bool:
    return bool(request.session.get("is_admin"))


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(f"{get_settings().root_path}{path}", status_code=303)


def require_admin(request: Request) -> bool:
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized — log in at /admin/login")
    return True


@router.get("/login")
async def login_page(request: Request):
    if _is_admin(request):
        return _redirect("/admin")
    return templates.TemplateResponse(request, "admin_login.html", {"error": False})


@router.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    password = (form.get("password") or "").strip()
    configured = get_settings().admin_password
    if configured and secrets.compare_digest(password, configured):
        request.session["is_admin"] = True
        return _redirect("/admin")
    return templates.TemplateResponse(
        request, "admin_login.html", {"error": True}, status_code=401
    )


@router.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return _redirect("/admin/login")


@router.get("")
async def admin_page(request: Request, db=Depends(get_db)):
    if not _is_admin(request):
        return _redirect("/admin/login")

    foods = [dict(r) for r in await db.execute_fetchall("SELECT * FROM foods ORDER BY name")]
    scans = [
        dict(r)
        for r in await db.execute_fetchall(
            "SELECT id, has_food, verdict, rating, created_at FROM scans ORDER BY id DESC LIMIT 30"
        )
    ]
    counts = {
        row["category"]: row["n"]
        for row in await db.execute_fetchall(
            "SELECT category, COUNT(*) AS n FROM foods GROUP BY category"
        )
    }
    return templates.TemplateResponse(
        request,
        "admin.html",
        {"foods": foods, "scans": scans, "counts": counts, "categories": _CATEGORIES},
    )


@router.post("/foods/add", dependencies=[Depends(require_admin)])
async def add_food(request: Request, db=Depends(get_db)):
    form = await request.form()
    name = (form.get("name") or "").strip()
    category = (form.get("category") or "").strip().lower()
    aliases = (form.get("aliases") or "").strip()
    notes = (form.get("notes") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Food name required")
    if category not in _CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category")

    existing = await db.execute_fetchall(
        "SELECT id FROM foods WHERE name = ? COLLATE NOCASE", (name,)
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"'{name}' is already on the list")
    await db.execute(
        "INSERT INTO foods (name, category, aliases, notes) VALUES (?, ?, ?, ?)",
        (name, category, aliases, notes),
    )
    await db.commit()
    return _redirect("/admin")


@router.post("/foods/{food_id}/delete", dependencies=[Depends(require_admin)])
async def delete_food(food_id: int, db=Depends(get_db)):
    existing = await db.execute_fetchall("SELECT id FROM foods WHERE id = ?", (food_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="Food not found")
    await db.execute("DELETE FROM foods WHERE id = ?", (food_id,))
    await db.commit()
    return _redirect("/admin")
