import inspect

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

    def _install(detect=None, identify=None, advice=None):
        if detect is not None:
            monkeypatch.setattr("app.routers.scan.llm.detect_food", _wrap(detect))
        if identify is not None:
            monkeypatch.setattr("app.routers.scan.llm.identify_foods", _wrap(identify))
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
    await init_db(str(db_path))

    from app.main import app
    from fastapi.testclient import TestClient

    with TestClient(app) as tc:
        yield tc


@pytest_asyncio.fixture
async def client(anon_client):
    """A TestClient already logged in to /admin (session cookie carries over
    to every subsequent request, same as a real browser)."""
    resp = anon_client.post(
        "/admin/login", data={"password": TEST_ADMIN_PASSWORD}, follow_redirects=False
    )
    assert resp.status_code == 303
    return anon_client


def scan_id_from(resp) -> int:
    """The scan redirect is /gout-stopper/scan/{id}; dig the id out."""
    location = resp.headers["location"]
    return int(location.rsplit("/", 1)[1])
