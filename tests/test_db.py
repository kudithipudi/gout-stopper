"""Unit tests for the DB helpers added for the learned-foods knowledge base
and the scan result cache."""

import sqlite3

from app.db import (
    connect,
    find_cached_scan,
    get_learned_foods,
    init_db,
    record_learned_food,
)


async def test_migrate_adds_input_hash_to_old_db(tmp_path):
    """A database created before input_hash existed gets the column (and its
    index) back on the next startup, without losing rows."""
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(db_path)
    raw.executescript(
        """CREATE TABLE scans (id INTEGER PRIMARY KEY AUTOINCREMENT, verdict TEXT);
           INSERT INTO scans (verdict) VALUES ('safe');"""
    )
    raw.commit()
    raw.close()

    await init_db(str(db_path))

    conn = await connect(str(db_path))
    cols = {r["name"] for r in await conn.execute_fetchall("PRAGMA table_info(scans)")}
    assert "input_hash" in cols
    idx = {
        r["name"]
        for r in await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='scans'"
        )
    }
    assert "idx_scans_input_hash" in idx
    kept = await conn.execute_fetchall("SELECT COUNT(*) AS n FROM scans")
    assert kept[0]["n"] == 1
    await conn.close()


async def test_record_learned_food_votes_and_decay(db):
    await record_learned_food(db, name="natto", category="limit", reason="fermented soy", delta=1)
    learned = await get_learned_foods(db)
    assert [f["name"] for f in learned] == ["natto"]
    assert learned[0]["category"] == "limit"

    # A second 👍 keeps it; two net 👎 past parity removes it.
    await record_learned_food(db, name="natto", category="limit", reason="", delta=1)
    for _ in range(4):
        await record_learned_food(db, name="natto", category="limit", reason="", delta=-1)
    assert await get_learned_foods(db) == []


async def test_record_learned_food_ignores_bad_category(db):
    await record_learned_food(db, name="x", category="bogus", reason="", delta=1)
    assert await get_learned_foods(db) == []


async def test_find_cached_scan_respects_age_and_verdict(db):
    await db.execute(
        "INSERT INTO scans (input_hash, verdict, advice) VALUES (?, 'avoid', 'skip it')",
        ("hash-a",),
    )
    await db.execute(
        "INSERT INTO scans (input_hash, verdict) VALUES (?, 'error')", ("hash-b",)
    )
    # A stale hit, ~40 days old.
    await db.execute(
        "INSERT INTO scans (input_hash, verdict, created_at) VALUES (?, 'safe',"
        " strftime('%Y-%m-%dT%H:%M:%SZ', 'now', '-960 hours'))",
        ("hash-c",),
    )
    await db.commit()

    hit = await find_cached_scan(db, "hash-a", max_age_hours=720)
    assert hit and hit["verdict"] == "avoid"

    assert await find_cached_scan(db, "hash-b", max_age_hours=720) is None
    assert await find_cached_scan(db, "hash-c", max_age_hours=720) is None
    assert await find_cached_scan(db, "", max_age_hours=720) is None
