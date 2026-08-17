import io
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from PIL import Image, UnidentifiedImageError

from app.config import get_settings
from app.db import check_and_record_rate_limit, get_db, get_scan
from app.services import llm, matcher
from app.services import ratelimit

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["prefix"] = get_settings().root_path


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(f"{get_settings().root_path}{path}", status_code=303)


async def rate_limit_scan(request: Request, db=Depends(get_db)) -> None:
    """FastAPI dependency: caps /scan requests per client IP. Reads the limit
    from get_settings() on every call (settings are deliberately not cached),
    so tests that monkeypatch SCAN_RATE_LIMIT_PER_MINUTE take effect
    immediately. Shares a "scan" bucket with /scan/text, same as before."""
    ip = ratelimit.client_ip(request)
    limit = get_settings().scan_rate_limit_per_minute
    ok = await check_and_record_rate_limit(
        db, ip=ip, route="scan", limit=limit, window_seconds=60
    )
    if not ok:
        raise HTTPException(
            status_code=429, detail="Too many scans — please wait a bit and try again."
        )


@router.post("/scan", dependencies=[Depends(rate_limit_scan)])
async def create_scan(request: Request, db=Depends(get_db)):
    form = await request.form()
    upload = form.get("image")
    if upload is None or not getattr(upload, "filename", ""):
        raise HTTPException(status_code=400, detail="No image selected")
    raw = await upload.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty image")
    if len(raw) > get_settings().max_upload_bytes:
        raise HTTPException(status_code=400, detail="Image is too large (max 20 MB)")

    try:
        Image.open(io.BytesIO(raw)).verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(
            status_code=400, detail="That doesn't look like a valid image — try a different photo."
        )

    mime = (upload.content_type or "image/jpeg").lower().split(";")[0].strip()
    ext = ".jpg"
    if mime == "image/png":
        ext = ".png"
    elif mime == "image/webp":
        ext = ".webp"
    elif mime == "image/gif":
        ext = ".gif"

    uploads = Path(get_settings().uploads_dir)
    uploads.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    image_path = uploads / filename
    image_path.write_bytes(raw)
    rel_path = f"uploads/{filename}"

    detect = await llm.detect_food(raw, mime)
    if detect is None:
        scan_id = await _store_scan(
            db,
            image_path=rel_path,
            has_food=None,
            detected=[],
            matched=[],
            verdict="error",
            error="Could not analyze the photo (LLM not reachable or not configured).",
        )
        return _redirect(f"/scan/{scan_id}")

    if not detect.get("has_food"):
        scan_id = await _store_scan(
            db, image_path=rel_path, has_food=False, detected=[], matched=[], verdict="no_food"
        )
        return _redirect(f"/scan/{scan_id}")

    items = await llm.identify_foods(raw, mime)
    rows = await db.execute_fetchall("SELECT * FROM foods ORDER BY name")
    foods = [dict(r) for r in rows]
    matched = matcher.match_detected(items, foods)
    verdict = matcher.overall_verdict(matched)
    advice, _ = await llm.generate_advice(items, matched)

    scan_id = await _store_scan(
        db,
        image_path=rel_path,
        has_food=True,
        detected=items,
        matched=matched,
        verdict=verdict,
        advice=advice,
    )
    return _redirect(f"/scan/{scan_id}")


async def _store_scan(
    db,
    *,
    image_path: str | None,
    query_text: str = "",
    has_food: bool | None,
    detected: list[dict],
    matched: list[dict],
    verdict: str,
    advice: str = "",
    error: str = "",
) -> int:
    import json

    settings = get_settings()
    cursor = await db.execute(
        """INSERT INTO scans
           (image_path, query_text, has_food, detected_items, matched_foods,
            advice, verdict, model_detect, model_identify, model_advice, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            image_path,
            query_text,
            1 if has_food else (0 if has_food is False else None),
            json.dumps(detected),
            json.dumps(matched),
            advice,
            verdict,
            settings.food_detect_model,
            settings.food_identify_model,
            settings.advice_model,
            error,
        ),
    )
    await db.commit()
    return cursor.lastrowid


@router.post("/scan/text", dependencies=[Depends(rate_limit_scan)])
async def create_text_scan(request: Request, db=Depends(get_db)):
    form = await request.form()
    text = (form.get("food") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Enter what you plan to eat")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="That description is too long (max 1000 chars)")

    items = await llm.identify_foods_from_text(text)
    if items is None:
        scan_id = await _store_scan(
            db,
            image_path=None,
            query_text=text,
            has_food=None,
            detected=[],
            matched=[],
            verdict="error",
            error="Could not analyze that (LLM not reachable or not configured).",
        )
        return _redirect(f"/scan/{scan_id}")
    if not items:
        scan_id = await _store_scan(
            db,
            image_path=None,
            query_text=text,
            has_food=False,
            detected=[],
            matched=[],
            verdict="no_food",
            error="",
        )
        return _redirect(f"/scan/{scan_id}")

    rows = await db.execute_fetchall("SELECT * FROM foods ORDER BY name")
    foods = [dict(r) for r in rows]
    matched = matcher.match_detected(items, foods)
    verdict = matcher.overall_verdict(matched)
    advice, _ = await llm.generate_advice(items, matched)

    scan_id = await _store_scan(
        db,
        image_path=None,
        query_text=text,
        has_food=True,
        detected=items,
        matched=matched,
        verdict=verdict,
        advice=advice,
    )
    return _redirect(f"/scan/{scan_id}")


@router.get("/scan/{scan_id}")
async def scan_result(request: Request, scan_id: int, db=Depends(get_db)):
    scan = await get_scan(db, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return templates.TemplateResponse(request, "result.html", {"scan": scan})


@router.post("/scan/{scan_id}/rate")
async def rate_scan(scan_id: int, request: Request, db=Depends(get_db)):
    form = await request.form()
    rating = (form.get("rating") or "").strip()
    if rating not in ("good", "bad"):
        raise HTTPException(status_code=400, detail="Invalid rating")
    existing = await db.execute_fetchall("SELECT id FROM scans WHERE id = ?", (scan_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="Scan not found")
    await db.execute("UPDATE scans SET rating = ? WHERE id = ?", (rating, scan_id))
    await db.commit()
    return _redirect(f"/scan/{scan_id}")
