import pytest_asyncio
from fastapi.testclient import TestClient

from app.db import init_db
from app.services import ratelimit
from tests.conftest import TEST_ADMIN_PASSWORD, csrf_token_from, scan_id_from
from tests.test_routes import FAKE_JPEG


@pytest_asyncio.fixture
async def limited_client(tmp_path, monkeypatch):
    """Same shape as anon_client, but with a tiny rate limit so we can
    actually trip it, and with the shared in-process limiter state cleared
    before and after so this test doesn't leak into (or get polluted by)
    other tests sharing the same TestClient "IP"."""
    db_path = tmp_path / "app-test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SCAN_RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setenv("ADMIN_LOGIN_RATE_LIMIT_PER_MINUTE", "1000")
    await init_db(str(db_path))

    from app.main import app

    ratelimit.reset()
    with TestClient(app) as tc:
        yield tc
    ratelimit.reset()


@pytest_asyncio.fixture
async def login_limited_client(tmp_path, monkeypatch):
    """Same idea as limited_client, but with a tiny *login* limit."""
    db_path = tmp_path / "app-test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("SCAN_RATE_LIMIT_PER_MINUTE", "1000")
    monkeypatch.setenv("ADMIN_LOGIN_RATE_LIMIT_PER_MINUTE", "3")
    await init_db(str(db_path))

    from app.main import app

    ratelimit.reset()
    with TestClient(app) as tc:
        yield tc
    ratelimit.reset()


def _upload(client):
    return client.post(
        "/scan",
        files={"image": ("plate.jpg", FAKE_JPEG, "image/jpeg")},
        follow_redirects=False,
    )


def test_scan_rate_limit_trips_on_third_request(limited_client, fake_llm):
    fake_llm(detect=lambda *a: {"has_food": False, "reason": "nothing edible"})

    first = _upload(limited_client)
    assert first.status_code == 303
    scan_id_from(first)

    second = _upload(limited_client)
    assert second.status_code == 303

    third = _upload(limited_client)
    assert third.status_code == 429
    assert "too many scans" in third.text.lower()


def test_scan_rate_limit_resets_between_windows(limited_client, fake_llm, monkeypatch):
    import time

    fake_llm(detect=lambda *a: {"has_food": False, "reason": "nothing edible"})

    assert _upload(limited_client).status_code == 303
    assert _upload(limited_client).status_code == 303
    assert _upload(limited_client).status_code == 429

    # Simulate the window elapsing by fast-forwarding the monotonic clock
    # the limiter reads from.
    real_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + 61)

    assert _upload(limited_client).status_code == 303


# --- admin login throttle -------------------------------------------------


def _login(client, password=TEST_ADMIN_PASSWORD):
    token = csrf_token_from(client.get("/admin/login").text)
    return client.post(
        "/admin/login",
        data={"password": password, "csrf_token": token},
        follow_redirects=False,
    )


def test_admin_login_throttles_after_limit(login_limited_client):
    # Every POST counts, pass or fail — this is an endpoint rate cap, not a
    # failed-attempts lockout.
    for _ in range(3):
        assert _login(login_limited_client, "wrong-password").status_code == 401

    fourth = _login(login_limited_client, "wrong-password")
    assert fourth.status_code == 429
    assert "too many login attempts" in fourth.text.lower()

    # Even the correct password is refused while throttled.
    assert _login(login_limited_client).status_code == 429


def test_admin_login_throttle_uses_its_own_bucket(login_limited_client, fake_llm):
    """The login limiter must not share a bucket with /scan's bare-IP keys."""
    fake_llm(detect=lambda *a: {"has_food": False, "reason": "nothing edible"})

    for _ in range(5):
        assert _upload(login_limited_client).status_code == 303

    # Scans burned no login budget: a successful login still works.
    assert _login(login_limited_client).status_code == 303


def test_admin_login_throttle_resets_between_windows(login_limited_client, monkeypatch):
    import time

    for _ in range(3):
        assert _login(login_limited_client, "wrong-password").status_code == 401
    assert _login(login_limited_client).status_code == 429

    real_monotonic = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real_monotonic() + 61)

    assert _login(login_limited_client).status_code == 303
