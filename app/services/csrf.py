"""Minimal session-bound CSRF tokens for the admin forms.

The admin area authenticates with a cookie-backed session (SessionMiddleware),
which browsers attach to cross-site form posts too — so without a token, any
page on the internet could make a logged-in admin's browser add or delete
foods. A per-session random token that must be echoed back in the form body
closes that: a cross-origin attacker can make the browser send the cookie, but
cannot read the token out of our HTML to include it.

Deliberately hand-rolled and dependency-free — it's ~30 lines, and this app
runs one gunicorn worker with no external infra.

The token lives in the signed session cookie and is reused for the whole
session rather than rotated per request, so multiple open tabs / a form left
sitting around keep working. It is rotated once on successful login, since the
pre-login and post-login sessions are different privilege levels.
"""

import secrets

from fastapi import HTTPException, Request

SESSION_KEY = "csrf_token"
FORM_FIELD = "csrf_token"


def get_token(request: Request) -> str:
    """Return this session's CSRF token, minting one if it doesn't have one
    yet. Safe to call on an anonymous session (e.g. GET /admin/login): the
    token only proves "this POST came from a page we rendered", not "this
    visitor is an admin"."""
    token = request.session.get(SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_KEY] = token
    return token


def rotate_token(request: Request) -> str:
    """Force a fresh token (used right after a successful login)."""
    request.session.pop(SESSION_KEY, None)
    return get_token(request)


def verify(request: Request, submitted: object) -> bool:
    """Timing-safe compare of a submitted token against the session's."""
    expected = request.session.get(SESSION_KEY)
    if not expected or not isinstance(submitted, str) or not submitted:
        return False
    return secrets.compare_digest(submitted, expected)


async def require_csrf(request: Request) -> None:
    """FastAPI dependency for admin POST routes. 403 (not 401) on failure:
    this means "the form was stale or tampered with", not "you aren't logged
    in". Starlette caches the parsed form on the request, so routes that also
    read `await request.form()` don't pay for a second parse."""
    form = await request.form()
    if not verify(request, form.get(FORM_FIELD)):
        raise HTTPException(
            status_code=403,
            detail="Invalid or expired form — please refresh and try again.",
        )
