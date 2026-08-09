import inspect
import re

import pytest
import pytest_asyncio

from app.config import get_settings
from app.db import connect, init_db

TEST_ADMIN_PASSWORD = "test-admin-password"


@pytest_asyncio.fixture
async def db(tmp_path):
    db_path = tmp_path / "test.db"
    await init_db(str(db_path))
    conn = await connect(str(db_path))
    yield conn
    await conn.close()


def _wrap(fn):
    if inspect.iscoroutinefunction(fn):
        return fn

    async def wrapper(*args, **kwargs):
        return fn(*args, **kwargs)

    return wrapper


@pytest.fixture
def fake_llm(monkeypatch):
    """Point the scan router's LLM helpers at canned responses, so tests never
    hit the network."""

    def _install(detect=None, identify=None, identify_text=None, advice=None):
        if detect is not None:
            monkeypatch.setattr("app.routers.scan.llm.detect_food", _wrap(detect))
        if identify is not None:
            monkeypatch.setattr("app.routers.scan.llm.identify_foods", _wrap(identify))
        if identify_text is not None:
            monkeypatch.setattr(
                "app.routers.scan.llm.identify_foods_from_text", _wrap(identify_text)
            )
        if advice is not None:
            monkeypatch.setattr("app.routers.scan.llm.generate_advice", _wrap(advice))

    return _install


@pytest_asyncio.fixture
async def anon_client(tmp_path, monkeypatch):
    """A TestClient with no admin session — for exercising what a visitor who
    hasn't logged in can and can't reach."""
    db_path = tmp_path / "app-test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    # High enough that the existing functional tests (which each make a
    # handful of /scan calls, all reported from the same TestClient "IP")
    # never trip the limiter. The limiter itself gets its own dedicated
    # tests in tests/test_ratelimit.py with a small override.
    monkeypatch.setenv("SCAN_RATE_LIMIT_PER_MINUTE", "1000")
    # Likewise for the admin login throttle: every test logs in through the
    # same TestClient "IP", so the production default of 5/min would start
    # returning 429s partway through the suite. The throttle gets its own
    # dedicated test in tests/test_ratelimit.py with a small override.
    monkeypatch.setenv("ADMIN_LOGIN_RATE_LIMIT_PER_MINUTE", "1000")
    await init_db(str(db_path))

    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        yield tc


def csrf_token_from(html: str) -> str:
    """Pull the hidden csrf_token value out of a rendered admin page, the same
    way a browser submitting the form would."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "expected a csrf_token hidden input in the rendered page"
    return match.group(1)


def login(tc) -> None:
    """Do the full browser-shaped login: fetch the form to pick up a CSRF
    token (and the session cookie carrying it), then post it back."""
    token = csrf_token_from(tc.get("/admin/login").text)
    resp = tc.post(
        "/admin/login",
        data={"password": TEST_ADMIN_PASSWORD, "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303


def admin_csrf(tc) -> str:
    """The current CSRF token as rendered on the admin dashboard."""
    return csrf_token_from(tc.get("/admin").text)


@pytest_asyncio.fixture
async def client(anon_client):
    """A TestClient already logged in to /admin (session cookie carries over
    to every subsequent request, same as a real browser)."""
    login(anon_client)
    return anon_client


def scan_id_from(resp) -> int:
    """The scan redirect is /gout-stopper/scan/{id}; dig the id out."""
    location = resp.headers["location"]
    return int(location.rsplit("/", 1)[1])
