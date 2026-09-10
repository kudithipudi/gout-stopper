import io

from PIL import Image
from app import __version__

from tests.conftest import (
    TEST_ADMIN_PASSWORD,
    admin_csrf,
    csrf_token_from,
    scan_id_from,
)


def _make_fake_jpeg() -> bytes:
    """A genuinely decodable tiny JPEG, so Pillow's validation in
    create_scan() accepts it — a fake/minimal header isn't enough."""
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(200, 30, 30)).save(buf, format="JPEG")
    return buf.getvalue()


FAKE_JPEG = _make_fake_jpeg()


async def _upload(client):
    return await client.post(
        "/scan",
        files={"image": ("plate.jpg", FAKE_JPEG, "image/jpeg")},
        follow_redirects=False,
    )


async def _text_scan(client, food="a cheeseburger with fries"):
    return await client.post(
        "/scan/text",
        data={"food": food},
        follow_redirects=False,
    )


# --- public pages ---------------------------------------------------------


async def test_health_ok(anon_client):
    resp = await anon_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__


async def test_index_ok(anon_client):
    resp = await anon_client.get("/")
    assert resp.status_code == 200
    assert "GoutStopper" in resp.text
    assert "capture" in resp.text


async def test_index_has_single_image_form_field(anon_client):
    """The camera and gallery-upload inputs must not both carry name="image" —
    duplicate keys let the browser submit an empty file field alongside the
    real one, and FastAPI's form parsing resolves duplicates to the last
    value, silently dropping the real upload and causing a 400."""
    resp = await anon_client.get("/")
    assert resp.text.count('name="image"') == 1


async def test_dynamic_pages_are_no_store_but_static_is_not(anon_client):
    """Cloudflare fronts the deploy — rendered pages must not be edge-cached
    (a release would otherwise serve stale HTML), while /static keeps caching."""
    home = await anon_client.get("/")
    assert home.headers.get("cache-control") == "no-store"

    asset = await anon_client.get("/static/js/history.js")
    assert asset.status_code == 200
    assert asset.headers.get("cache-control") != "no-store"


async def test_about_ok(anon_client):
    resp = await anon_client.get("/about")
    assert resp.status_code == 200
    assert "gout" in resp.text.lower()
    assert "medical" in resp.text.lower()


async def test_unknown_scan_404(anon_client):
    assert (await anon_client.get("/scan/9999")).status_code == 404


async def test_offline_ok(anon_client):
    resp = await anon_client.get("/offline")
    assert resp.status_code == 200
    assert "offline" in resp.text.lower()


async def test_manifest(anon_client):
    resp = await anon_client.get("/manifest.webmanifest")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/manifest+json")
    body = resp.json()
    assert body["name"] == "GoutStopper"
    assert body["display"] == "standalone"
    assert len(body["icons"]) >= 2


async def test_service_worker(anon_client):
    resp = await anon_client.get("/sw.js")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/javascript")
    assert "addEventListener" in resp.text


async def test_base_page_links_manifest_and_registers_sw(anon_client):
    resp = await anon_client.get("/")
    assert 'rel="manifest"' in resp.text
    assert "serviceWorker" in resp.text


async def test_base_page_has_install_banner(anon_client):
    resp = await anon_client.get("/")
    assert "installBanner()" in resp.text
    assert "beforeinstallprompt" in resp.text


# --- scan lifecycle -------------------------------------------------------


async def test_scan_requires_image(anon_client):
    resp = await anon_client.post("/scan", follow_redirects=False)
    assert resp.status_code == 400


async def test_scan_rejects_non_image_bytes(anon_client):
    resp = await anon_client.post(
        "/scan",
        files={"image": ("plate.jpg", b"not actually an image, just text bytes", "image/jpeg")},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "look like a valid image" in resp.text


def test_downscale_for_model_helper():
    from app.services.images import downscale_for_model

    assert downscale_for_model(FAKE_JPEG) is None  # 8x8, already tiny

    buf = io.BytesIO()
    Image.new("RGB", (3000, 2000), (10, 20, 30)).save(buf, format="JPEG")
    out = downscale_for_model(buf.getvalue())
    assert out is not None
    assert max(Image.open(io.BytesIO(out)).size) == 1280
    assert len(out) < 3000 * 2000  # and much smaller on the wire


def test_thumbnail_helper_reencodes_to_small_jpeg():
    from app.services.images import thumbnail

    buf = io.BytesIO()
    Image.new("RGB", (2000, 1500), (10, 20, 30)).save(buf, format="PNG")
    out = thumbnail(buf.getvalue())
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG"
    assert max(img.size) == 640


async def test_preview_endpoint_returns_a_jpeg_thumbnail(anon_client):
    buf = io.BytesIO()
    Image.new("RGB", (1200, 900), (30, 120, 60)).save(buf, format="PNG")
    resp = await anon_client.post(
        "/scan/preview",
        files={"image": ("plate.png", buf.getvalue(), "image/png")},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert max(Image.open(io.BytesIO(resp.content)).size) == 640


async def test_preview_endpoint_rejects_non_image(anon_client):
    resp = await anon_client.post(
        "/scan/preview",
        files={"image": ("x.jpg", b"still not an image", "image/jpeg")},
    )
    assert resp.status_code == 400


async def test_large_photo_is_downscaled_server_side(anon_client, fake_llm):
    from pathlib import Path

    from app.config import get_settings

    buf = io.BytesIO()
    Image.new("RGB", (2400, 1800), (120, 60, 30)).save(buf, format="JPEG")
    fake_llm(analyze=lambda raw, mime: {"has_food": False, "reason": "n/a", "foods": []})

    resp = await anon_client.post(
        "/scan", files={"image": ("big.jpg", buf.getvalue(), "image/jpeg")}, follow_redirects=False
    )
    assert resp.status_code == 303

    stored = sorted(Path(get_settings().uploads_dir).glob("*"))
    assert stored, "expected the scan to write a stored image"
    assert max(Image.open(stored[-1]).size) <= 1280


async def test_scan_no_food(anon_client, fake_llm):
    fake_llm(detect=lambda *a: {"has_food": False, "reason": "nothing edible"})
    resp = await _upload(anon_client)
    assert resp.status_code == 303
    sid = scan_id_from(resp)

    page = await anon_client.get(f"/scan/{sid}")
    assert page.status_code == 200
    assert "No food found" in page.text


async def test_scan_avoid_verdict(anon_client, fake_llm):
    async def detect(*a):
        return {"has_food": True, "reason": "a meal"}

    async def identify(*a):
        return [{"name": "beer", "confidence": 0.98}, {"name": "salmon", "confidence": 0.9}]

    async def advice(*a):
        return "Skip the beer; the salmon is fine in moderation.", "avoid"

    fake_llm(detect=detect, identify=identify, advice=advice)
    resp = await _upload(anon_client)
    assert resp.status_code == 303
    sid = scan_id_from(resp)

    page = await anon_client.get(f"/scan/{sid}")
    assert page.status_code == 200
    assert "avoid" in page.text
    assert "beer" in page.text.lower()


async def test_scan_estimated_fallback(anon_client, fake_llm):
    """A detected food that's on no list gets its verdict from the LLM
    classifier, and the result page marks it as an estimate."""

    async def analyze(raw, mime):
        return {
            "has_food": True,
            "reason": "a plate",
            "foods": [{"name": "kimchi", "confidence": 0.9, "portion": ""}],
        }

    async def classify(names):
        assert names == ["kimchi"]
        return {"kimchi": {"category": "limit", "reason": "moderate-purine fermented cabbage"}}

    fake_llm(analyze=analyze, classify=classify)
    resp = await _upload(anon_client)
    sid = scan_id_from(resp)

    page = await anon_client.get(f"/scan/{sid}")
    assert "caution" in page.text  # verdict rolled up from the 'limit' estimate
    assert "est." in page.text
    assert "moderate-purine fermented cabbage" in page.text


async def test_scan_records_the_classify_model(anon_client, fake_llm):
    """Every model that shaped a result is stored on the scan row — the cache
    key already includes the classify model, so the audit trail must too."""
    import sqlite3

    from app.config import get_settings

    async def analyze(raw, mime):
        return {"has_food": True, "reason": "", "foods": [{"name": "kimchi", "confidence": 0.9, "portion": ""}]}

    async def classify(names):
        return {"kimchi": {"category": "limit", "reason": "fermented cabbage"}}

    fake_llm(analyze=analyze, classify=classify)
    sid = scan_id_from(await _upload(anon_client))

    con = sqlite3.connect(get_settings().db_path)
    row = con.execute("SELECT model_classify FROM scans WHERE id = ?", (sid,)).fetchone()
    con.close()
    assert row[0] == get_settings().gout_classify_model


async def test_good_ratings_train_learned_foods_after_net_two(anon_client, fake_llm):
    """👍 on an estimated result records the food; once its net support reaches
    +2, the next identical scan resolves from the learned list without calling
    the classifier again. A single 👍 is not enough."""

    async def analyze(raw, mime):
        return {
            "has_food": True,
            "reason": "a bowl",
            "foods": [{"name": "natto", "confidence": 0.9, "portion": ""}],
        }

    calls = {"n": 0}

    async def classify(names):
        calls["n"] += 1
        return {"natto": {"category": "limit", "reason": "moderate-purine fermented soy"}}

    fake_llm(analyze=analyze, classify=classify)

    async def scan_and_upvote():
        sid = scan_id_from(await _upload(anon_client))
        rated = await anon_client.post(
            f"/scan/{sid}/rate", data={"rating": "good"}, follow_redirects=False
        )
        assert rated.status_code == 303
        return sid

    await scan_and_upvote()
    assert calls["n"] == 1

    await scan_and_upvote()
    assert calls["n"] == 2, "one upvote must not short-circuit the classifier"

    sid3 = scan_id_from(await _upload(anon_client))
    assert calls["n"] == 2, "net +2 should have short-circuited the classifier"

    page = await anon_client.get(f"/scan/{sid3}")
    assert "limit" in page.text
    assert "learned" in page.text


async def test_scan_llm_down(anon_client, fake_llm):
    fake_llm(detect=lambda *a: None)
    resp = await _upload(anon_client)
    assert resp.status_code == 303
    sid = scan_id_from(resp)

    page = await anon_client.get(f"/scan/{sid}")
    assert page.status_code == 200
    assert "couldn't analyze" in page.text.lower()


async def test_scan_rate(anon_client, fake_llm):
    async def identify(*a):
        return [{"name": "kimchi", "confidence": 0.9}]

    fake_llm(detect=lambda *a: {"has_food": True, "reason": "meal"}, identify=identify)
    resp = await _upload(anon_client)
    sid = scan_id_from(resp)

    rated = await anon_client.post(
        f"/scan/{sid}/rate", data={"rating": "good"}, follow_redirects=False
    )
    assert rated.status_code == 303

    page = await anon_client.get(f"/scan/{sid}")
    assert "your feedback is saved" in page.text
    assert "border-emerald-300" in page.text  # "Good" button is highlighted
    assert "border-rose-300" not in page.text


async def test_scan_rate_invalid(anon_client, fake_llm):
    async def identify(*a):
        return [{"name": "kimchi", "confidence": 0.9}]

    fake_llm(detect=lambda *a: {"has_food": True, "reason": "meal"}, identify=identify)
    resp = await _upload(anon_client)
    sid = scan_id_from(resp)

    assert (
        await anon_client.post(f"/scan/{sid}/rate", data={"rating": "meh"})
    ).status_code == 400
    assert (
        await anon_client.post("/scan/9999/rate", data={"rating": "good"})
    ).status_code == 404


# --- text scan ------------------------------------------------------------


async def test_text_scan_requires_text(anon_client):
    resp = await anon_client.post("/scan/text", follow_redirects=False)
    assert resp.status_code == 400
    resp = await anon_client.post("/scan/text", data={"food": "   "}, follow_redirects=False)
    assert resp.status_code == 400


async def test_text_scan_rejects_overlong_text(anon_client):
    resp = await anon_client.post(
        "/scan/text",
        data={"food": "x" * 1001},
        follow_redirects=False,
    )
    assert resp.status_code == 400


async def test_text_scan_verdict(anon_client, fake_llm):
    async def identify_text(text):
        return [{"name": "beer", "confidence": 0.98}, {"name": "salmon", "confidence": 0.9}]

    async def advice(*a):
        return "Skip the beer; the salmon is fine in moderation.", "avoid"

    fake_llm(identify_text=identify_text, advice=advice)
    resp = await _text_scan(anon_client, food="beer and salmon for dinner")
    assert resp.status_code == 303
    sid = scan_id_from(resp)

    page = await anon_client.get(f"/scan/{sid}")
    assert page.status_code == 200
    assert "beer and salmon for dinner" in page.text  # shows what they asked about
    assert "avoid" in page.text
    assert "beer" in page.text.lower()


async def test_large_portion_escalates_the_result_verdict(anon_client, fake_llm):
    """Salmon alone is 'caution'; a large stated portion pushes the scan to
    'avoid', and the result page says the portion was factored in."""

    async def identify_text(text):
        return [{"name": "salmon", "confidence": 0.9, "portion": "several large fillets"}]

    import sqlite3

    from app.config import get_settings

    fake_llm(identify_text=identify_text, advice=lambda *a: ("Go easy on the salmon.", "avoid"))
    sid = scan_id_from(await _text_scan(anon_client, food="several large salmon fillets"))

    con = sqlite3.connect(get_settings().db_path)
    verdict = con.execute("SELECT verdict FROM scans WHERE id = ?", (sid,)).fetchone()[0]
    con.close()
    assert verdict == "avoid", "large portion of a limit food should roll up to avoid"

    page = await anon_client.get(f"/scan/{sid}")
    assert "Some of this is on the “avoid” list" in page.text
    assert "Enjoy in moderation" not in page.text
    assert "Larger portions were counted toward this verdict" in page.text


async def test_normal_portion_leaves_the_verdict_alone(anon_client, fake_llm):
    async def identify_text(text):
        return [{"name": "salmon", "confidence": 0.9, "portion": "a fillet"}]

    fake_llm(identify_text=identify_text)
    sid = scan_id_from(await _text_scan(anon_client, food="a salmon fillet"))
    page = await anon_client.get(f"/scan/{sid}")
    assert "Enjoy in moderation" in page.text
    assert "counted toward this verdict" not in page.text


async def test_text_scan_nothing_identified(anon_client, fake_llm):
    fake_llm(identify_text=lambda text: [])
    resp = await _text_scan(anon_client, food="nothing much")
    assert resp.status_code == 303
    sid = scan_id_from(resp)

    page = await anon_client.get(f"/scan/{sid}")
    assert page.status_code == 200
    assert "No food found" in page.text


async def test_scan_cache_hit_skips_llm(anon_client, fake_llm, monkeypatch):
    monkeypatch.setenv("SCAN_CACHE_ENABLED", "1")
    calls = {"n": 0}

    async def identify_text(text):
        calls["n"] += 1
        return [{"name": "beer", "confidence": 0.98}]

    fake_llm(identify_text=identify_text, advice=lambda *a: ("Skip the beer.", "avoid"))

    first = await _text_scan(anon_client, food="a cold beer")
    sid1 = scan_id_from(first)
    assert calls["n"] == 1

    second = await _text_scan(anon_client, food="a cold beer")
    sid2 = scan_id_from(second)
    assert sid2 != sid1, "cache hit still writes its own scan row"
    assert calls["n"] == 1, "identical scan must not re-run the LLM"

    page = await anon_client.get(f"/scan/{sid2}")
    assert "avoid" in page.text
    assert "beer" in page.text.lower()


async def test_text_scan_llm_down(anon_client, fake_llm):
    fake_llm(identify_text=lambda text: None)
    resp = await _text_scan(anon_client)
    assert resp.status_code == 303
    sid = scan_id_from(resp)

    page = await anon_client.get(f"/scan/{sid}")
    assert page.status_code == 200
    assert "couldn't analyze" in page.text.lower()


# --- admin ----------------------------------------------------------------


async def test_admin_requires_login(anon_client):
    resp = await anon_client.get("/admin", follow_redirects=False)
    assert resp.status_code == 303
    assert "/admin/login" in resp.headers["location"]


async def test_admin_login_wrong_password(anon_client):
    token = csrf_token_from((await anon_client.get("/admin/login")).text)
    resp = await anon_client.post(
        "/admin/login",
        data={"password": "nope", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert (await anon_client.get("/admin", follow_redirects=False)).status_code == 303


async def test_admin_page_ok(client):
    resp = await client.get("/admin")
    assert resp.status_code == 200
    assert "Add a food" in resp.text
    assert "beer" in resp.text  # seeded baseline


async def test_admin_add_and_delete_food(client):
    token = await admin_csrf(client)
    add = await client.post(
        "/admin/foods/add",
        data={
            "name": "caviar",
            "category": "avoid",
            "aliases": "caviar roe",
            "notes": "",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert add.status_code == 303
    assert (await client.get("/admin")).text.count("caviar") >= 1

    dup = await client.post(
        "/admin/foods/add",
        data={
            "name": "caviar",
            "category": "avoid",
            "aliases": "",
            "notes": "",
            "csrf_token": token,
        },
    )
    assert dup.status_code == 409

    bad_cat = await client.post(
        "/admin/foods/add",
        data={
            "name": "x",
            "category": "bogus",
            "aliases": "",
            "notes": "",
            "csrf_token": token,
        },
    )
    assert bad_cat.status_code == 400

    # Locate the delete form for the row we just added and remove it.
    import re

    html = (await client.get("/admin")).text
    match = re.search(r"caviar</td>.*?foods/(\d+)/delete", html, re.DOTALL)
    assert match, "expected a delete action for caviar"
    food_id = int(match.group(1))

    dele = await client.post(
        f"/admin/foods/{food_id}/delete",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert dele.status_code == 303
    assert "caviar" not in (await client.get("/admin")).text


async def test_admin_promote_and_dismiss_learned_food(client, fake_llm):
    # Produce a learned entry the honest way: an estimated scan, then a 👍.
    async def identify_text(text):
        return [{"name": "escargot", "confidence": 0.9}]

    async def classify(names):
        return {"escargot": {"category": "avoid", "reason": "high-purine shellfish-like"}}

    fake_llm(identify_text=identify_text, classify=classify)
    sid = scan_id_from(await _text_scan(client, food="escargot"))
    await client.post(f"/scan/{sid}/rate", data={"rating": "good"}, follow_redirects=False)

    import re

    html = (await client.get("/admin")).text
    assert "escargot" in html
    match = re.search(r"escargot</td>.*?learned/(\d+)/promote", html, re.DOTALL)
    assert match, "expected a promote action for the learned row"
    lid = int(match.group(1))

    token = await admin_csrf(client)
    promoted = await client.post(
        f"/admin/learned/{lid}/promote", data={"csrf_token": token}, follow_redirects=False
    )
    assert promoted.status_code == 303
    after = (await client.get("/admin")).text
    assert "escargot" in after  # now on the authoritative Food list
    assert f"learned/{lid}/promote" not in after  # gone from the learned table

    # Promoting again (row already consumed) 404s.
    assert (
        await client.post(f"/admin/learned/{lid}/promote", data={"csrf_token": token})
    ).status_code == 404


async def test_admin_learned_table_flags_which_rows_are_active(client, fake_llm):
    """A learned row is only consulted once its net support hits +2. The admin
    table has to make that distinction visible, or a lone-upvote row looks
    like it's already in effect."""

    async def identify_text(text):
        return [{"name": "escargot", "confidence": 0.9}]

    async def classify(names):
        return {"escargot": {"category": "avoid", "reason": "high-purine"}}

    fake_llm(identify_text=identify_text, classify=classify)

    sid = scan_id_from(await _text_scan(client, food="escargot"))
    await client.post(f"/scan/{sid}/rate", data={"rating": "good"}, follow_redirects=False)

    html = (await client.get("/admin")).text
    assert "Pending" in html and "escargot" in html

    sid2 = scan_id_from(await _text_scan(client, food="escargot"))
    await client.post(f"/scan/{sid2}/rate", data={"rating": "good"}, follow_redirects=False)

    html = (await client.get("/admin")).text
    assert "Active" in html


async def test_admin_learned_routes_need_csrf(client):
    resp = await client.post("/admin/learned/1/promote", follow_redirects=False)
    assert resp.status_code == 403


async def test_admin_delete_missing(client):
    resp = await client.post(
        "/admin/foods/99999/delete", data={"csrf_token": await admin_csrf(client)}
    )
    assert resp.status_code == 404


async def test_admin_logout(client):
    resp = await client.post(
        "/admin/logout",
        data={"csrf_token": await admin_csrf(client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/admin/login" in resp.headers["location"]


# --- admin CSRF -----------------------------------------------------------


async def test_admin_csrf_token_is_rendered_in_forms(client):
    html = (await client.get("/admin")).text
    # logout + add-food + one per food row.
    assert html.count('name="csrf_token"') >= 3


async def test_login_page_renders_csrf_token_for_anonymous_visitor(anon_client):
    resp = await anon_client.get("/admin/login")
    assert resp.status_code == 200
    token = csrf_token_from(resp.text)
    assert token
    # And that token survives the cookie round-trip into the POST.
    assert (
        await anon_client.post(
            "/admin/login",
            data={"password": TEST_ADMIN_PASSWORD, "csrf_token": token},
            follow_redirects=False,
        )
    ).status_code == 303


async def test_admin_add_food_rejects_missing_csrf(client):
    resp = await client.post(
        "/admin/foods/add",
        data={"name": "sardines-x", "category": "avoid", "aliases": "", "notes": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert "refresh" in resp.text.lower()
    # ...and the mutation did not happen.
    assert "sardines-x" not in (await client.get("/admin")).text


async def test_admin_add_food_rejects_wrong_csrf(client):
    resp = await client.post(
        "/admin/foods/add",
        data={
            "name": "sardines-y",
            "category": "avoid",
            "aliases": "",
            "notes": "",
            "csrf_token": "not-the-real-token",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert "sardines-y" not in (await client.get("/admin")).text


async def test_admin_delete_food_rejects_missing_csrf(client):
    import re

    html = (await client.get("/admin")).text
    match = re.search(r"beer</td>.*?foods/(\d+)/delete", html, re.DOTALL)
    assert match, "expected a delete action for the seeded 'beer' row"
    food_id = int(match.group(1))

    resp = await client.post(f"/admin/foods/{food_id}/delete", follow_redirects=False)
    assert resp.status_code == 403
    assert "beer" in (await client.get("/admin")).text  # still there


async def test_admin_logout_rejects_missing_csrf(client):
    resp = await client.post("/admin/logout", follow_redirects=False)
    assert resp.status_code == 403
    # Session was not cleared: still an admin.
    assert (await client.get("/admin", follow_redirects=False)).status_code == 200


async def test_admin_login_rejects_missing_csrf(anon_client):
    resp = await anon_client.post(
        "/admin/login", data={"password": TEST_ADMIN_PASSWORD}, follow_redirects=False
    )
    assert resp.status_code == 403
    assert (await anon_client.get("/admin", follow_redirects=False)).status_code == 303


async def test_admin_csrf_checked_after_auth(anon_client):
    """An anonymous POST to a protected mutation still reads as 401, not 403 —
    require_admin runs before the CSRF check."""
    resp = await anon_client.post(
        "/admin/foods/add",
        data={"name": "nope", "category": "avoid", "aliases": "", "notes": ""},
    )
    assert resp.status_code == 401
