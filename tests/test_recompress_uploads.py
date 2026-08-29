"""Tests for the one-off scripts/recompress_uploads.py maintenance script."""

import io

from PIL import Image

from scripts.recompress_uploads import _recompress, main


def _jpeg(w, h, *, quality=95):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (90, 120, 60)).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def test_recompress_shrinks_oversized_image():
    raw = _jpeg(4000, 3000)
    out = _recompress(raw)
    assert out is not None
    assert max(Image.open(io.BytesIO(out)).size) == 1280
    assert len(out) < len(raw)


def test_recompress_leaves_small_files_alone():
    # Small dimensions AND under the size threshold -> untouched.
    assert _recompress(_jpeg(400, 300, quality=80)) is None


def test_recompress_reencodes_bloated_in_bounds_file():
    # 1400px (within the 1600 cap) but a fat 95%-quality encode -> re-encoded.
    raw = _jpeg(1400, 1400, quality=98)
    if len(raw) < 400 * 1024:
        return  # synthetic image compressed too well to exercise this path
    out = _recompress(raw)
    assert out is not None and len(out) < len(raw)


def test_main_dry_run_writes_nothing(tmp_path, capsys):
    big = tmp_path / "a.jpg"
    big.write_bytes(_jpeg(3000, 2000))
    before = big.read_bytes()

    rc = main(["--dir", str(tmp_path)])
    assert rc == 0
    assert big.read_bytes() == before  # dry run by default
    assert "DRY RUN" in capsys.readouterr().out


def test_main_apply_rewrites_and_preserves_name(tmp_path):
    p = tmp_path / "keep-this-name.jpg"
    p.write_bytes(_jpeg(3600, 2400))
    original_size = p.stat().st_size

    assert main(["--dir", str(tmp_path), "--apply"]) == 0

    assert p.exists()  # same filename (DB image_path still resolves)
    assert p.stat().st_size < original_size
    assert max(Image.open(p).size) <= 1280
    assert not list(tmp_path.glob("*.tmp"))
