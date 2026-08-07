# GoutStopper

Snap a photo of food; the app identifies what's on the plate and flags anything
that tends to trigger gout attacks. Educational tool — not medical advice.

## What it is

- Upload or capture a photo (mobile camera via `capture="environment"`).
- Three separate LLM purposes, each with its own configurable model:
  1. **detect** — is there any food in the photo at all? If not, say so.
  2. **identify** — list the distinct foods/drinks visible.
  3. **advice** — a short, friendly takeaway written for someone prone to gout.
- Detected items are matched **deterministically** (no LLM) against an
  admin-managed list of gout-relevant foods in three categories:
  **Avoid / Limit / OK**.
- Every scan is stored (image + results) and visitors can rate it 👍/👎 so the
  admin can measure accuracy over time.
- Admin area (password login) to add/delete foods and review recent scans +
  ratings.
- Basic gout information page with a clear "not medical advice" disclaimer.

## Stack

Python 3.12 · FastAPI · SQLite (aiosqlite) · Jinja2 + Tailwind + Alpine.js ·
OpenRouter vision models · gunicorn/uvicorn.

## Run locally

```
python3 -m venv venv
venv/bin/pip install -r requirements.txt
cp .env.example .env   # add OPENROUTER_API_KEY + ADMIN_PASSWORD
venv/bin/uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000/. The app also works behind a subpath: set
`ROOT_PATH=/gout-stopper` in `.env` for template URL prefixing.

### Rebuilding Tailwind CSS

Tailwind is compiled with the lab standalone CLI into the committed
`app/static/css/app.css` (no CDN). After editing templates/classes, rebuild:

```
/var/www/tailwindcss -i app/static/css/input.css -o app/static/css/app.css --minify
```

Alpine.js is vendored (pinned) at `app/static/js/alpine.min.js`.

## Tests

```
venv/bin/python -m pytest
```

The suite mocks the LLM (no network); it covers the public pages, the scan
lifecycle (no-food, avoid verdict, LLM-down, rating), and the admin login +
food CRUD.

## Deploy

- Served by nginx at `https://lab.kudithipudi.org/gout-stopper/` → unix socket
  `/var/www/gout-stopper/gout-stopper.sock` via systemd unit
  `gout-stopper.service` (runs as `www-data`).
- After code changes: `sudo systemctl restart gout-stopper`, then verify
  `curl -s -o /dev/null -w '%{http_code}' https://lab.kudithipudi.org/gout-stopper/`.

## Env vars (`.env`, chmod 600 www-data)

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | Required for scans. All LLM calls go through OpenRouter. |
| `ADMIN_PASSWORD` | Password for the `/admin` login form. |
| `SESSION_SECRET` | Signs the admin session cookie (stable secret avoids surprise logouts). |
| `ROOT_PATH` | Public subpath, default `/gout-stopper`. |
| `DB_PATH` | SQLite file, default `data/gout-stopper.db`. |
| `UPLOADS_DIR` | Stored scan photos, default `data/uploads`. |
| `FOOD_DETECT_MODEL` | Model for "is there food?" gate. Default `openai/gpt-4o-mini`. |
| `FOOD_IDENTIFY_MODEL` | Model for listing foods. Default `openai/gpt-4o-mini`. |
| `ADVICE_MODEL` | Model for the takeaway text. Default `openai/gpt-4o-mini`. |
| `LLM_TEMPERATURE` / `LLM_TIMEOUT` | Call tuning. |

Any OpenRouter model that accepts image content works (e.g.
`google/gemini-2.0-flash`, `openai/gpt-4o`); per-purpose models can differ.
