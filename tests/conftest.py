import inspect
import re

import httpx
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


@pytest.fixture(autouse=True)
def _no_llm_network(monkeypatch):
    """Hard stop on outbound OpenRouter calls in every test: the single choke
    point `llm._chat_json` returns None unless a test installs a fake above it
    via the `fake_llm` fixture."""

    async def _blocked(*args, **kwargs):  # pragma: no cover - safety net
        raise AssertionError("llm._chat_json called in a test without a fake_llm stub")

    monkeypatch.setattr("app.services.llm._chat_json", _blocked)


@pytest.fixture
def fake_llm(monkeypatch):
    """Point the scan router's LLM helpers at canned responses, so tests never
    hit the network.

    Accepts the current API (`analyze`, `identify_text`, `advice`, `classify`)
    and, for the pre-merge photo tests, a compatibility shim that synthesises
    `analyze_photo` from `detect` + `identify`. Anything not supplied is left as
    a safe no-op stub (advice -> ("", "safe"), classify -> {}, analyze/text ->
    None) so no test can leak a real network call."""

    def _install(
        detect=None, identify=None, analyze=None, identify_text=None, advice=None, classify=None
    ):
        if analyze is None and (detect is not None or identify is not None):
            _detect = _wrap(detect) if detect is not None else None
            _identify = _wrap(identify) if identify is not None else None

            async def analyze(raw, mime):
                d = await _detect(raw, mime) if _detect else {"has_food": True, "reason": ""}
                if d is None:
                    return None
                if not d.get("has_food"):
                    return {"has_food": False, "reason": d.get("reason", ""), "foods": []}
                foods = await _identify(raw, mime) if _identify else []
                return {"has_food": True, "reason": d.get("reason", ""), "foods": foods}

        async def _default_advice(*a, **k):
            return "", "safe"

        async def _default_classify(*a, **k):
            return {}

        async def _default_none(*a, **k):
            return None

        monkeypatch.setattr(
            "app.routers.scan.llm.analyze_photo", _wrap(analyze) if analyze is not None else _default_none
        )
        monkeypatch.setattr(
            "app.routers.scan.llm.identify_foods_from_text",
            _wrap(identify_text) if identify_text is not None else _default_none,
        )
        monkeypatch.setattr(
            "app.routers.scan.llm.generate_advice",
            _wrap(advice) if advice is not None else _default_advice,
        )
        monkeypatch.setattr(
            "app.routers.scan.llm.classify_gout_risk",
            _wrap(classify) if classify is not None else _default_classify,
        )

    return _install


@pytest_asyncio.fixture
async def anon_client(tmp_path, monkeypatch):
    """An httpx client with no admin session — for exercising what a visitor
    who hasn't logged in can and can't reach."""
    db_path = tmp_path / "app-test.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    # Off by default so functional tests that scan the same fixture image /
    # text twice still exercise the pipeline both times. The cache gets its
    # own dedicated tests that opt back in.
    monkeypatch.setenv("SCAN_CACHE_ENABLED", "0")
    # High enough that the existing functional tests (which each make a
    # handful of /scan calls, all reported from the same client "IP")
    # never trip the limiter. The limiter itself gets its own dedicated
    # tests in tests/test_ratelimit.py with a small override.
    monkeypatch.setenv("SCAN_RATE_LIMIT_PER_MINUTE", "1000")
    # Likewise for the admin login throttle: every test logs in through the
    # same client "IP", so the production default of 5/min would start
    # returning 429s partway through the suite. The throttle gets its own
    # dedicated test in tests/test_ratelimit.py with a small override.
    monkeypatch.setenv("ADMIN_LOGIN_RATE_LIMIT_PER_MINUTE", "1000")
    await init_db(str(db_path))

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as tc:
        yield tc


def csrf_token_from(html: str) -> str:
    """Pull the hidden csrf_token value out of a rendered admin page, the same
    way a browser submitting the form would."""
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "expected a csrf_token hidden input in the rendered page"
    return match.group(1)


async def login(tc) -> None:
    """Do the full browser-shaped login: fetch the form to pick up a CSRF
    token (and the session cookie carrying it), then post it back."""
    token = csrf_token_from((await tc.get("/admin/login")).text)
    resp = await tc.post(
        "/admin/login",
        data={"password": TEST_ADMIN_PASSWORD, "csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 303


async def admin_csrf(tc) -> str:
    """The current CSRF token as rendered on the admin dashboard."""
    return csrf_token_from((await tc.get("/admin")).text)


@pytest_asyncio.fixture
async def client(anon_client):
    """A client already logged in to /admin (session cookie carries over
    to every subsequent request, same as a real browser)."""
    await login(anon_client)
    return anon_client


def scan_id_from(resp) -> int:
    """The scan redirect is /gout-stopper/scan/{id}; dig the id out."""
    location = resp.headers["location"]
    return int(location.rsplit("/", 1)[1])
