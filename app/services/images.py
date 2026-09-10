"""Upload image handling for the scan routes: pull the file out of the form,
confirm it is a real image, and turn whatever a phone or browser sent into
something both the vision model and the browser can use.

A vision model gains nothing past ~1280px for identifying food, so anything
noticeably larger is capped before it is stored and base64'd to the model.
HEIC/HEIF captures (common on iOS-synced photos) aren't renderable in browsers
or accepted by the model, so they are converted to JPEG.
"""

import io

from fastapi import HTTPException, Request
from PIL import Image, UnidentifiedImageError
import pillow_heif

from app.config import get_settings

pillow_heif.register_heif_opener()

_EXT_BY_MIME = {"image/png": ".png", "image/webp": ".webp", "image/gif": ".gif"}

# Server-side safety net for the client-side resize in index.html: a desktop
# upload, a JS-disabled browser, or a direct API call can still send a 12 MP /
# multi-MB capture.
_STORE_MAX_DIM = 1600
_STORE_TARGET_DIM = 1280


async def read_upload(request: Request) -> tuple[bytes, str, str]:
    """Pull the uploaded image out of the form, enforce the size cap, and
    confirm it decodes. Returns (raw_bytes, pillow_format, content_type).
    Raises HTTPException(400) on anything that isn't a usable image."""
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
        fmt = Image.open(io.BytesIO(raw)).format or ""
        Image.open(io.BytesIO(raw)).verify()
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="That doesn't look like a valid image — try a different photo.",
        )
    return raw, fmt, (upload.content_type or "image/jpeg")


def prepare_for_model(raw: bytes, fmt: str, content_type: str) -> tuple[bytes, str, str]:
    """Normalise a validated upload for storage + the vision model: HEIC/HEIF
    to JPEG, then downscale anything much larger than the model can use.
    Returns (raw_bytes, mime, file_extension)."""
    mime = content_type.lower().split(";")[0].strip()
    ext = _EXT_BY_MIME.get(mime, ".jpg")

    if fmt == "HEIF":
        converted = Image.open(io.BytesIO(raw)).convert("RGB")
        buf = io.BytesIO()
        converted.save(buf, format="JPEG", quality=90)
        raw, mime, ext = buf.getvalue(), "image/jpeg", ".jpg"

    downscaled = downscale_for_model(raw)
    if downscaled is not None:
        raw, mime, ext = downscaled, "image/jpeg", ".jpg"

    return raw, mime, ext


def downscale_for_model(raw: bytes) -> bytes | None:
    """Return a JPEG capped at `_STORE_TARGET_DIM` on the long edge, or None if
    the image is already within `_STORE_MAX_DIM` (or Pillow can't re-encode it).
    """
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    if max(img.size) <= _STORE_MAX_DIM:
        return None
    img = img.convert("RGB")
    img.thumbnail((_STORE_TARGET_DIM, _STORE_TARGET_DIM))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def thumbnail(raw: bytes, *, max_dim: int = 640, quality: int = 80) -> bytes:
    """A browser-safe JPEG thumbnail of an uploaded photo, for formats the
    browser can't decode itself (e.g. HEIC/HEIF). Raises HTTPException(400)
    if the bytes don't decode."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="That doesn't look like a valid image — try a different photo.",
        )
    img = img.convert("RGB")
    img.thumbnail((max_dim, max_dim))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()
