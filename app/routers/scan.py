import hashlib
import json
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.db import (
    check_and_record_rate_limit,
    find_cached_scan,
    get_db,
    get_learned_foods,
    get_scan,
    record_learned_food,
)
from app.services import images, llm, matcher
from app.services import ratelimit

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
templates.env.globals["prefix"] = get_settings().root_path


def _redirect(path: str) -> RedirectResponse:
    return RedirectResponse(f"{get_settings().root_path}{path}", status_code=303)


def _input_hash(payload: bytes) -> str:
    """Cache key: the model line-up plus the (kind-prefixed) input. A model
    change or a different photo/description produces a different hash."""
    s = get_settings()
    key = (
        f"{s.food_detect_model}|{s.food_identify_model}|"
        f"{s.advice_model}|{s.gout_classify_model}|"
    ).encode()
    return hashlib.sha256(key + payload).hexdigest()


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
    raw, fmt, content_type = await images.read_upload(request)
    raw, mime, ext = images.prepare_for_model(raw, fmt, content_type)

    uploads = Path(get_settings().uploads_dir)
    uploads.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    (uploads / filename).write_bytes(raw)
    rel_path = f"uploads/{filename}"

    input_hash = _input_hash(b"photo:" + raw)
    cached = await _cache_hit(db, input_hash)
    if cached is not None:
        scan_id = await _store_from_cache(db, cached, image_path=rel_path, input_hash=input_hash)
        return _redirect(f"/scan/{scan_id}")

    analysis = await llm.analyze_photo(raw, mime)
    if analysis is None:
        scan_id = await _store_error(
            db,
            image_path=rel_path,
            input_hash=input_hash,
            message="Could not analyze the photo (LLM not reachable or not configured).",
        )
        return _redirect(f"/scan/{scan_id}")

    if not analysis["has_food"]:
        scan_id = await _store_no_food(db, image_path=rel_path, input_hash=input_hash)
        return _redirect(f"/scan/{scan_id}")

    scan_id = await _run_pipeline(
        db,
        image_path=rel_path,
        query_text="",
        items=analysis["foods"],
        input_hash=input_hash,
    )
    return _redirect(f"/scan/{scan_id}")


@router.post("/scan/preview", dependencies=[Depends(rate_limit_scan)])
async def preview_scan_image(request: Request):
    """Returns a browser-safe JPEG thumbnail of an uploaded photo, for
    formats the browser can't decode itself (e.g. HEIC/HEIF) — the client
    calls this only when it already failed to render its own preview."""
    raw, _fmt, _ct = await images.read_upload(request)
    return Response(content=images.thumbnail(raw), media_type="image/jpeg")


async def _cache_hit(db, input_hash: str) -> dict | None:
    settings = get_settings()
    if not settings.scan_cache_enabled:
        return None
    return await find_cached_scan(
        db, input_hash, max_age_hours=settings.scan_cache_max_age_hours
    )


async def _store_from_cache(db, cached: dict, *, image_path=None, query_text="", input_hash="") -> int:
    """Persist a fresh scan row that reuses a recent identical scan's results,
    without any LLM calls."""
    has_food = cached.get("has_food")
    return await _store_scan(
        db,
        image_path=image_path,
        query_text=query_text,
        input_hash=input_hash,
        has_food=None if has_food is None else bool(has_food),
        detected=cached.get("detected_items") or [],
        matched=cached.get("matched_foods") or [],
        verdict=cached.get("verdict"),
        advice=cached.get("advice") or "",
    )


async def _run_pipeline(db, *, image_path, query_text, items: list[dict], input_hash: str) -> int:
    """Layered lookup for identified foods: admin list -> feedback-trained list
    -> LLM estimate. Then verdict + advice + persist. Returns the new scan id."""
    rows = await db.execute_fetchall("SELECT * FROM foods ORDER BY name")
    foods = [dict(r) for r in rows]
    matched = matcher.match_detected(items, foods, source="list")

    unknown_idx = [i for i, m in enumerate(matched) if m["category"] == "unknown"]

    if unknown_idx:
        learned = await get_learned_foods(db)
        if learned:
            relayer = matcher.match_detected(
                [items[i] for i in unknown_idx], learned, source="learned"
            )
            for pos, i in enumerate(unknown_idx):
                if relayer[pos]["category"] != "unknown":
                    matched[i] = relayer[pos]
        unknown_idx = [i for i in unknown_idx if matched[i]["category"] == "unknown"]

    if unknown_idx:
        ratings = await llm.classify_gout_risk([matched[i]["item"] for i in unknown_idx])
        for i in unknown_idx:
            rated = ratings.get(matched[i]["item"])
            if rated:
                matched[i] = {
                    **matched[i],
                    "category": rated["category"],
                    "source": "estimated",
                    "reason": rated.get("reason", ""),
                }

    verdict = matcher.overall_verdict(matched)
    advice, _ = await llm.generate_advice(items, matched)

    return await _store_scan(
        db,
        image_path=image_path,
        query_text=query_text,
        input_hash=input_hash,
        has_food=True,
        detected=items,
        matched=matched,
        verdict=verdict,
        advice=advice,
    )


async def _store_scan(
    db,
    *,
    image_path: str | None,
    query_text: str = "",
    input_hash: str = "",
    has_food: bool | None,
    detected: list[dict],
    matched: list[dict],
    verdict: str | None,
    advice: str = "",
    error: str = "",
) -> int:
    settings = get_settings()
    cursor = await db.execute(
        """INSERT INTO scans
           (image_path, query_text, input_hash, has_food, detected_items, matched_foods,
            advice, verdict, model_detect, model_identify, model_advice, model_classify, error)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            image_path,
            query_text,
            input_hash,
            1 if has_food else (0 if has_food is False else None),
            json.dumps(detected),
            json.dumps(matched),
            advice,
            verdict,
            settings.food_detect_model,
            settings.food_identify_model,
            settings.advice_model,
            settings.gout_classify_model,
            error,
        ),
    )
    await db.commit()
    return cursor.lastrowid


async def _store_error(db, *, image_path=None, query_text="", input_hash: str, message: str) -> int:
    return await _store_scan(
        db,
        image_path=image_path,
        query_text=query_text,
        input_hash=input_hash,
        has_food=None,
        detected=[],
        matched=[],
        verdict="error",
        error=message,
    )


async def _store_no_food(db, *, image_path=None, query_text="", input_hash: str) -> int:
    return await _store_scan(
        db,
        image_path=image_path,
        query_text=query_text,
        input_hash=input_hash,
        has_food=False,
        detected=[],
        matched=[],
        verdict="no_food",
    )


@router.post("/scan/text", dependencies=[Depends(rate_limit_scan)])
async def create_text_scan(request: Request, db=Depends(get_db)):
    form = await request.form()
    text = (form.get("food") or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Enter what you plan to eat")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="That description is too long (max 1000 chars)")

    input_hash = _input_hash(b"text:" + matcher.normalize(text).encode())
    cached = await _cache_hit(db, input_hash)
    if cached is not None:
        scan_id = await _store_from_cache(
            db, cached, query_text=text, input_hash=input_hash
        )
        return _redirect(f"/scan/{scan_id}")

    items = await llm.identify_foods_from_text(text)
    if items is None:
        scan_id = await _store_error(
            db,
            query_text=text,
            input_hash=input_hash,
            message="Could not analyze that (LLM not reachable or not configured).",
        )
        return _redirect(f"/scan/{scan_id}")
    if not items:
        scan_id = await _store_no_food(db, query_text=text, input_hash=input_hash)
        return _redirect(f"/scan/{scan_id}")

    scan_id = await _run_pipeline(
        db, image_path=None, query_text=text, items=items, input_hash=input_hash
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
    scan = await get_scan(db, scan_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    await db.execute("UPDATE scans SET rating = ? WHERE id = ?", (rating, scan_id))
    await db.commit()

    # A 👍/👎 on a result that leaned on an LLM estimate trains the learned
    # list, so the next identical food resolves without paying for a call.
    delta = 1 if rating == "good" else -1
    for m in scan.get("matched_foods") or []:
        if m.get("source") == "estimated" and m.get("category") in ("avoid", "limit", "ok"):
            await record_learned_food(
                db,
                name=m["item"],
                category=m["category"],
                reason=m.get("reason", ""),
                delta=delta,
            )
    return _redirect(f"/scan/{scan_id}")
