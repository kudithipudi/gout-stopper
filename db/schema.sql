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

-- One row per photo scan. detected/matched payloads are JSON text; rating lets
-- users flag a scan as good/bad so the admin can measure accuracy over time.
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    image_path TEXT,
    -- User-typed food description (text scans). NULL for photo scans.
    query_text TEXT,
    has_food INTEGER,
    -- JSON list: [{"name": "...", "confidence": 0.95}]
    detected_items TEXT NOT NULL DEFAULT '[]',
    -- JSON list: [{"item": "...", "category": "avoid"|"limit"|"ok"|"unknown", "matches": [...]}]
    matched_foods TEXT NOT NULL DEFAULT '[]',
    advice TEXT NOT NULL DEFAULT '',
    -- 'no_food' | 'safe' | 'caution' | 'avoid' | 'error'
    verdict TEXT,
    model_detect TEXT,
    model_identify TEXT,
    model_advice TEXT,
    error TEXT,
    -- 'good' | 'bad' | NULL (user rating)
    rating TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
