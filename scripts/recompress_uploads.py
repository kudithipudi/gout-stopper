#!/usr/bin/env python3
"""One-off maintenance: recompress stored scan photos in-place.

Scans created before the client/server 1280px cap kept multi-MB originals in
`data/uploads/`. This walks that directory and rewrites each photo the same way
`app/routers/scan.py:_downscale_for_model` now does for new uploads:

  * long edge capped at 1280px (only touched when it exceeds 1600px)
  * EXIF orientation baked in, metadata stripped
  * re-encoded as JPEG q85

Filenames are preserved (the DB's `scans.image_path` points at them). A file is
only rewritten when that actually saves space; writes are atomic and keep the
original owner and mtime.

    # preview what would change
    venv/bin/python -m scripts.recompress_uploads

    # do it
    venv/bin/python -m scripts.recompress_uploads --apply
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

# Kept in step with app/routers/scan.py.
STORE_MAX_DIM = 1600
STORE_TARGET_DIM = 1280
JPEG_QUALITY = 85
# Skip small files that aren't oversized — re-encoding them rarely helps.
MIN_BYTES_TO_CONSIDER = 400 * 1024
# Only rewrite when the result is at least this much smaller.
MIN_SAVING_RATIO = 0.95

_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def _recompress(raw: bytes) -> bytes | None:
    """Return smaller JPEG bytes, or None to leave the file untouched."""
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None

    oversized = max(img.size) > STORE_MAX_DIM
    if not oversized and len(raw) < MIN_BYTES_TO_CONSIDER:
        return None

    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > STORE_MAX_DIM:
        img.thumbnail((STORE_TARGET_DIM, STORE_TARGET_DIM))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    out = buf.getvalue()

    if not oversized and len(out) >= len(raw) * MIN_SAVING_RATIO:
        return None
    return out


def _atomic_write(path: Path, data: bytes) -> None:
    st = path.stat()
    tmp = path.with_name(path.name + ".recompress.tmp")
    tmp.write_bytes(data)
    try:
        os.chown(tmp, st.st_uid, st.st_gid)
    except (PermissionError, OSError):
        pass
    os.utime(tmp, (st.st_atime, st.st_mtime))
    os.replace(tmp, path)


def _uploads_dir(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit)
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.config import get_settings

    return Path(get_settings().uploads_dir)


def _fmt(n: int) -> str:
    return f"{n / 1024:,.0f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.1f} MB"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    parser.add_argument("--dir", help="uploads directory (default: from app config / UPLOADS_DIR)")
    args = parser.parse_args(argv)

    directory = _uploads_dir(args.dir)
    if not directory.is_dir():
        print(f"not a directory: {directory}", file=sys.stderr)
        return 1

    files = sorted(p for p in directory.iterdir() if p.suffix.lower() in _SUFFIXES and p.is_file())
    print(f"{'APPLY' if args.apply else 'DRY RUN'} — {len(files)} image(s) in {directory}\n")

    changed = before_total = after_total = 0
    for path in files:
        raw = path.read_bytes()
        new = _recompress(raw)
        if new is None:
            continue
        changed += 1
        before_total += len(raw)
        after_total += len(new)
        pct = 100 * (1 - len(new) / len(raw))
        print(f"  {path.name}  {_fmt(len(raw))} -> {_fmt(len(new))}  (-{pct:.0f}%)")
        if args.apply:
            _atomic_write(path, new)

    if not changed:
        print("nothing to do — every file is already within bounds")
        return 0

    print(
        f"\n{changed} file(s) {'rewritten' if args.apply else 'would shrink'}: "
        f"{_fmt(before_total)} -> {_fmt(after_total)} "
        f"(saves {_fmt(before_total - after_total)})"
    )
    if not args.apply:
        print("re-run with --apply to write the changes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
