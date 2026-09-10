-- Canonical schema for GoutStopper.
-- Applied idempotently on startup via app/db.py (CREATE TABLE IF NOT EXISTS).

-- Admin-managed gout food list. Category drives the verdict shown to users.
CREATE TABLE IF NOT EXISTS foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'avoid'
        CHECK (category IN ('avoid', 'limit', 'ok')),
    -- Comma-separated common names / plural forms used to match detected items.
    aliases TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_foods_name ON foods (name COLLATE NOCASE);

-- Foods learned from user 👍 feedback on LLM-estimated results. Advisory only:
-- consulted after the admin `foods` list but before paying for an LLM call, so
-- repeat scans of the same off-list food get faster and more consistent. The
-- admin can promote a row into `foods` or dismiss it (see app/routers/admin.py).
CREATE TABLE IF NOT EXISTS learned_foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,                        -- normalized (matcher.normalize)
    category TEXT NOT NULL
        CHECK (category IN ('avoid', 'limit', 'ok')),
    reason TEXT NOT NULL DEFAULT '',
    upvotes INTEGER NOT NULL DEFAULT 0,
    downvotes INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_learned_foods_name ON learned_foods (name);

-- Sliding-window log backing per-IP rate limiting (see
-- app/db.py:check_and_record_rate_limit). One row per hit, not fixed buckets.
CREATE TABLE IF NOT EXISTS rate_limit_hits (
    ip TEXT NOT NULL,
    route TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_rate_limit_hits_route_ip_time
    ON rate_limit_hits (route, ip, created_at);

-- One row per photo scan. detected/matched payloads are JSON text; rating lets
-- users flag a scan as good/bad so the admin can measure accuracy over time.
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_path TEXT,
    -- User-typed food description (text scans). NULL for photo scans.
    query_text TEXT,
    -- sha256 of (model triplet + normalized text | raw image bytes). Lets an
    -- identical repeat scan reuse a recent result without re-running the LLM.
    input_hash TEXT,
    has_food INTEGER,
    -- JSON list: [{"name": "...", "confidence": 0.95, "portion": "a pint"}]
    detected_items TEXT NOT NULL DEFAULT '[]',
    -- JSON list: [{"item": "...", "category": "avoid"|"limit"|"ok"|"unknown",
    --             "matches": [...], "source": "list"|"learned"|"estimated"|"unknown",
    --             "reason": "..."  (estimated only)}]
    matched_foods TEXT NOT NULL DEFAULT '[]',
    advice TEXT NOT NULL DEFAULT '',
    -- 'no_food' | 'safe' | 'caution' | 'avoid' | 'error'
    verdict TEXT,
    model_detect TEXT,
    model_identify TEXT,
    model_advice TEXT,
    model_classify TEXT,
    error TEXT,
    -- 'good' | 'bad' | NULL (user rating)
    rating TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- NOTE: idx_scans_input_hash is created in app/db.py:_migrate(), not here —
-- executescript() runs before the additive ALTER that adds input_hash to
-- databases created before the column existed.
