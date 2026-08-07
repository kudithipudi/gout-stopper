import io

from PIL import Image

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


def _upload(client):
    return client.post(
        "/scan",
        files={"image": ("plate.jpg", FAKE_JPEG, "image/jpeg")},
        follow_redirects=False,
    )


# --- public pages ---------------------------------------------------------


def test_index_ok(anon_client):
    resp = anon_client.get("/")
    assert resp.status_code == 200
    assert "GoutStopper" in resp.text
    assert "capture" in resp.text


def test_about_ok(anon_client):
    resp = anon_client.get("/about")
    assert resp.status_code == 200
    assert "gout" in resp.text.lower()
    assert "medical" in resp.text.lower()


def test_unknown_scan_404(anon_client):
    assert anon_client.get("/scan/9999").status_code == 404


# --- scan lifecycle -------------------------------------------------------


def test_scan_requires_image(anon_client):
    resp = anon_client.post("/scan", follow_redirects=False)
    assert resp.status_code == 400


def test_scan_rejects_non_image_bytes(anon_client):
    resp = anon_client.post(
        "/scan",
        files={"image": ("plate.jpg", b"not actually an image, just text bytes", "image/jpeg")},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    assert "look like a valid image" in resp.text


def test_scan_no_food(anon_client, fake_llm):
    fake_llm(detect=lambda *a: {"has_food": False, "reason": "nothing edible"})
    resp = _upload(anon_client)
    assert resp.status_code == 303
    sid = scan_id_from(resp)

    page = anon_client.get(f"/scan/{sid}")
    assert page.status_code == 200
    assert "No food detected" in page.text


def test_scan_avoid_verdict(anon_client, fake_llm):
    async def detect(*a):
        return {"has_food": True, "reason": "a meal"}

    async def identify(*a):
        return [{"name": "beer", "confidence": 0.98}, {"name": "salmon", "confidence": 0.9}]

    async def advice(*a):
        return "Skip the beer; the salmon is fine in moderation.", "avoid"

    fake_llm(detect=detect, identify=identify, advice=advice)
    resp = _upload(anon_client)
    assert resp.status_code == 303
    sid = scan_id_from(resp)

    page = anon_client.get(f"/scan/{sid}")
    assert page.status_code == 200
    assert "avoid" in page.text
    assert "beer" in page.text.lower()


def test_scan_llm_down(anon_client, fake_llm):
    fake_llm(detect=lambda *a: None)
    resp = _upload(anon_client)
    assert resp.status_code == 303
    sid = scan_id_from(resp)

    page = anon_client.get(f"/scan/{sid}")
    assert page.status_code == 200
    assert "couldn't analyze" in page.text.lower()


def test_scan_rate(anon_client, fake_llm):
    async def identify(*a):
        return [{"name": "kimchi", "confidence": 0.9}]

    fake_llm(detect=lambda *a: {"has_food": True, "reason": "meal"}, identify=identify)
    resp = _upload(anon_client)
    sid = scan_id_from(resp)

    rated = anon_client.post(f"/scan/{sid}/rate", data={"rating": "good"}, follow_redirects=False)
    assert rated.status_code == 303

    page = anon_client.get(f"/scan/{sid}")
    assert "your feedback is saved" in page.text
    assert "border-emerald-300" in page.text  # "Good" button is highlighted
    assert "border-rose-300" not in page.text


def test_scan_rate_invalid(anon_client, fake_llm):
    async def identify(*a):
        return [{"name": "kimchi", "confidence": 0.9}]

    fake_llm(detect=lambda *a: {"has_food": True, "reason": "meal"}, identify=identify)
    resp = _upload(anon_client)
    sid = scan_id_from(resp)

    assert anon_client.post(f"/scan/{sid}/rate", data={"rating": "meh"}).status_code == 400
    assert anon_client.post("/scan/9999/rate", data={"rating": "good"}).status_code == 404


# --- admin ----------------------------------------------------------------


def test_admin_requires_login(anon_client):
    resp = anon_client.get("/admin", follow_redirects=False)
    assert resp.status_code == 303
    assert "/admin/login" in resp.headers["location"]


def test_admin_login_wrong_password(anon_client):
    token = csrf_token_from(anon_client.get("/admin/login").text)
    resp = anon_client.post(
        "/admin/login",
        data={"password": "nope", "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 401
    assert anon_client.get("/admin", follow_redirects=False).status_code == 303


def test_admin_page_ok(client):
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "Add a food" in resp.text
    assert "beer" in resp.text  # seeded baseline


def test_admin_add_and_delete_food(client):
    token = admin_csrf(client)
    add = client.post(
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
    assert client.get("/admin").text.count("caviar") >= 1

    dup = client.post(
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

    bad_cat = client.post(
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

    html = client.get("/admin").text
    match = re.search(r"caviar</td>.*?foods/(\d+)/delete", html, re.DOTALL)
    assert match, "expected a delete action for caviar"
    food_id = int(match.group(1))

    dele = client.post(
        f"/admin/foods/{food_id}/delete",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert dele.status_code == 303
    assert "caviar" not in client.get("/admin").text


def test_admin_delete_missing(client):
    resp = client.post("/admin/foods/99999/delete", data={"csrf_token": admin_csrf(client)})
    assert resp.status_code == 404


def test_admin_logout(client):
    resp = client.post(
        "/admin/logout",
        data={"csrf_token": admin_csrf(client)},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert "/admin/login" in resp.headers["location"]


# --- admin CSRF -----------------------------------------------------------


def test_admin_csrf_token_is_rendered_in_forms(client):
    html = client.get("/admin").text
    # logout + add-food + one per food row.
    assert html.count('name="csrf_token"') >= 3


def test_login_page_renders_csrf_token_for_anonymous_visitor(anon_client):
    resp = anon_client.get("/admin/login")
    assert resp.status_code == 200
    token = csrf_token_from(resp.text)
    assert token
    # And that token survives the cookie round-trip into the POST.
    assert (
        anon_client.post(
            "/admin/login",
            data={"password": TEST_ADMIN_PASSWORD, "csrf_token": token},
            follow_redirects=False,
        ).status_code
        == 303
    )


def test_admin_add_food_rejects_missing_csrf(client):
    resp = client.post(
        "/admin/foods/add",
        data={"name": "sardines-x", "category": "avoid", "aliases": "", "notes": ""},
        follow_redirects=False,
    )
    assert resp.status_code == 403
    assert "refresh" in resp.text.lower()
    # ...and the mutation did not happen.
    assert "sardines-x" not in client.get("/admin").text


def test_admin_add_food_rejects_wrong_csrf(client):
    resp = client.post(
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
    assert "sardines-y" not in client.get("/admin").text


def test_admin_delete_food_rejects_missing_csrf(client):
    import re

    html = client.get("/admin").text
    match = re.search(r"beer</td>.*?foods/(\d+)/delete", html, re.DOTALL)
    assert match, "expected a delete action for the seeded 'beer' row"
    food_id = int(match.group(1))

    resp = client.post(f"/admin/foods/{food_id}/delete", follow_redirects=False)
    assert resp.status_code == 403
    assert "beer" in client.get("/admin").text  # still there


def test_admin_logout_rejects_missing_csrf(client):
    resp = client.post("/admin/logout", follow_redirects=False)
    assert resp.status_code == 403
    # Session was not cleared: still an admin.
    assert client.get("/admin", follow_redirects=False).status_code == 200


def test_admin_login_rejects_missing_csrf(anon_client):
    resp = anon_client.post(
        "/admin/login", data={"password": TEST_ADMIN_PASSWORD}, follow_redirects=False
    )
    assert resp.status_code == 403
    assert anon_client.get("/admin", follow_redirects=False).status_code == 303


def test_admin_csrf_checked_after_auth(anon_client):
    """An anonymous POST to a protected mutation still reads as 401, not 403 —
    require_admin runs before the CSRF check."""
    resp = anon_client.post(
        "/admin/foods/add",
        data={"name": "nope", "category": "avoid", "aliases": "", "notes": ""},
    )
    assert resp.status_code == 401
