"""Deterministic matching of LLM-detected food names against the admin-managed
gout list. Pure string logic — no LLM involved, so it is cheap and predictable.
"""

import re

_STRIP = re.compile(r"[^a-z0-9 ]+")
_WORD = re.compile(r"[a-z0-9]+")

_CATEGORY_PRIORITY = {"avoid": 3, "limit": 2, "ok": 1, "unknown": 0}


def normalize(name: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace, strip a trailing 's'."""
    s = _STRIP.sub(" ", name.lower())
    s = re.sub(r"\s+", " ", s).strip()
    if s.endswith("s") and len(s) > 3:
        s = s[:-1]
    return s


def _tokens(s: str) -> set[str]:
    return set(_WORD.findall(s))


def _food_variants(food: dict) -> set[str]:
    variants = {food["name"]}
    for alias in (food.get("aliases") or "").split(","):
        alias = alias.strip()
        if alias:
            variants.add(alias)
    return {normalize(v) for v in variants if v}


def match_detected(detected_items: list[dict], foods: list[dict]) -> list[dict]:
    """For each detected item, find the best food match (highest-priority
    category wins when several list entries match). 'unknown' when nothing
    matches. Returns one dict per detected item:
    {"item": original name, "category": ..., "matches": [matched food names]}
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
                cat_pri = _CATEGORY_PRIORITY.get(food["category"], 0)
                if normalized == variant:
                    best = _promote(best, (cat_pri, food["category"], food["name"]))
                elif _substring(normalized, variant):
                    best = _promote(best, (cat_pri, food["category"], food["name"]))
                elif _token_overlap(normalized, variant):
                    best = _promote(best, (cat_pri, food["category"], food["name"]))

        rows.append(
            {
                "item": name,
                "confidence": round(float(item.get("confidence") or 0.5), 2),
                "category": best[1],
                "matches": best[2],
            }
        )
    return rows


def _promote(current: tuple[int, str, list[str]], cand: tuple[int, str, str]) -> tuple[int, str, list[str]]:
    if cand[0] > current[0]:
        return (cand[0], cand[1], [cand[2]])
    if cand[0] == current[0] and cand[2] not in current[2]:
        return (current[0], current[1], current[2] + [cand[2]])
    return current


def _substring(a: str, b: str) -> bool:
    if len(a) < 3 or len(b) < 3:
        return False
    return a in b or b in a


def _token_overlap(a: str, b: str) -> bool:
    """True when the two names share a meaningful (>=4 char) word, e.g.
    "grilled salmon" ~ "salmon". Short tokens are handled by _substring."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    return bool({t for t in (ta & tb) if len(t) >= 4})


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
