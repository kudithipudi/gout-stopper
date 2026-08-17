import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import check_and_record_rate_limit, get_db
from app.services import csrf, ratelimit

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


async def rate_limit_login(request: Request, db=Depends(get_db)) -> None:
    """Throttle POSTs to the login endpoint per client IP.

    Deliberately counts *every* attempt, successful or not — this is a rate cap
    on the endpoint, not an "N failed attempts then lock the account out"
    scheme. What actually bounds brute-force is total guesses per minute, and
    counting only failures would let an attacker who ever guesses right reset
    their budget. A legitimate human fumbling their password a few times stays
    well under the limit.

    Uses its own "admin_login" route bucket, separate from the "scan" bucket
    /scan and /scan/text use.
    """
    ip = ratelimit.client_ip(request)
    limit = get_settings().admin_login_rate_limit_per_minute
    ok = await check_and_record_rate_limit(
        db, ip=ip, route="admin_login", limit=limit, window_seconds=60
    )
    if not ok:
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts — please wait a bit and try again.",
        )


@router.get("/login")
async def login_page(request: Request):
    if _is_admin(request):
        return _redirect("/admin")
    return templates.TemplateResponse(
        request,
        "admin_login.html",
        {"error": False, "csrf_token": csrf.get_token(request)},
    )


@router.post(
    "/login",
    # Order matters: throttle before doing any other work, so a flood of
    # requests is cheap to reject.
    dependencies=[Depends(rate_limit_login), Depends(csrf.require_csrf)],
)
async def login_submit(request: Request):
    form = await request.form()
    password = (form.get("password") or "").strip()
    configured = get_settings().admin_password
    if configured and secrets.compare_digest(password, configured):
        request.session["is_admin"] = True
        # New privilege level, new token.
        csrf.rotate_token(request)
        return _redirect("/admin")
    return templates.TemplateResponse(
        request,
        "admin_login.html",
        {"error": True, "csrf_token": csrf.get_token(request)},
        status_code=401,
    )


@router.post("/logout", dependencies=[Depends(csrf.require_csrf)])
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
        {
            "foods": foods,
            "scans": scans,
            "counts": counts,
            "categories": _CATEGORIES,
            "csrf_token": csrf.get_token(request),
        },
    )


@router.post("/foods/add", dependencies=[Depends(require_admin), Depends(csrf.require_csrf)])
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


@router.post(
    "/foods/{food_id}/delete",
    dependencies=[Depends(require_admin), Depends(csrf.require_csrf)],
)
async def delete_food(food_id: int, db=Depends(get_db)):
    existing = await db.execute_fetchall("SELECT id FROM foods WHERE id = ?", (food_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="Food not found")
    await db.execute("DELETE FROM foods WHERE id = ?", (food_id,))
    await db.commit()
    return _redirect("/admin")
