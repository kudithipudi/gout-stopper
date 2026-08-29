"""Deterministic matching of LLM-detected food names against the admin-managed
gout list. Pure string logic — no LLM involved, so it is cheap and predictable.
"""

import re

_STRIP = re.compile(r"[^a-z0-9 ]+")

_CATEGORY_PRIORITY = {"avoid": 3, "limit": 2, "ok": 1, "unknown": 0}


def normalize(name: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace, strip a trailing 's'."""
    s = _STRIP.sub(" ", name.lower())
    s = re.sub(r"\s+", " ", s).strip()
    if s.endswith("s") and len(s) > 3:
        s = s[:-1]
    return s


def _food_variants(food: dict) -> set[str]:
    variants = {food["name"]}
    for alias in (food.get("aliases") or "").split(","):
        alias = alias.strip()
        if alias:
            variants.add(alias)
    return {normalize(v) for v in variants if v}


def match_detected(
    detected_items: list[dict], foods: list[dict], *, source: str = "list"
) -> list[dict]:
    """For each detected item, find the best food match (highest-priority
    category wins when several list entries match). 'unknown' when nothing
    matches. Returns one dict per detected item:
    {"item": original name, "category": ..., "matches": [...], "source": ...}
    `source` labels where a matched category came from ("list", "learned",
    "estimated"); unmatched rows are always tagged "unknown".
    """
    rows = []
    for item in detected_items:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        normalized = normalize(name)
        best: tuple[int, str, list[str]] = (0, "unknown", [])

        for food in foods:
            for variant in _food_variants(food):
                if len(variant) < 3:
                    continue
                if normalized == variant or _phrase_contains(normalized, variant):
                    cat_pri = _CATEGORY_PRIORITY.get(food["category"], 0)
                    best = _promote(best, (cat_pri, food["category"], food["name"]))

        matched = best[1] != "unknown"
        rows.append(
            {
                "item": name,
                "confidence": round(float(item.get("confidence") or 0.5), 2),
                "category": best[1],
                "matches": best[2],
                "source": source if matched else "unknown",
            }
        )
    return rows


def _promote(current: tuple[int, str, list[str]], cand: tuple[int, str, str]) -> tuple[int, str, list[str]]:
    if cand[0] > current[0]:
        return (cand[0], cand[1], [cand[2]])
    if cand[0] == current[0] and cand[2] not in current[2]:
        return (current[0], current[1], current[2] + [cand[2]])
    return current


def _phrase_contains(haystack: str, needle: str) -> bool:
    """True when `needle` appears in `haystack` as a whole word or phrase:
    "salmon" in "grilled salmon" -> yes, but "wine" ~ "white rice" and
    "chicken stock" ~ "grilled chicken thigh" -> no. A single word shared
    between two different phrases is deliberately NOT a match — that's what
    used to rate "white rice" as wine and "chicken thigh" as broth.
    """
    return re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", haystack) is not None


def overall_verdict(matched: list[dict]) -> str:
    """Roll per-item categories into a single scan verdict."""
    if not matched:
        return "no_food"
    cats = {m["category"] for m in matched}
    if "avoid" in cats:
        return "avoid"
    if "limit" in cats:
        return "caution"
    if cats and cats == {"unknown"}:
        return "caution"  # food present but nothing matched the list
    return "safe"
