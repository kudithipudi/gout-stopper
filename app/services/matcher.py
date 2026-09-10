"""Deterministic matching of LLM-detected food names against the admin-managed
gout list. Pure string logic — no LLM involved, so it is cheap and predictable.
"""

import re

_STRIP = re.compile(r"[^a-z0-9 ]+")

_CATEGORY_PRIORITY = {"avoid": 3, "limit": 2, "ok": 1, "unknown": 0}

# Words that name a whole category of dish rather than a specific food. A
# learned-list entry this broad does more harm than good: "soup" learned as
# "limit" would then flag tomato soup, miso soup, gazpacho — anything with the
# word in it. `normalize()` has already stripped a trailing plural 's', so the
# singular forms are what we check against.
_GENERIC_FOOD_WORDS = {
    "soup", "stew", "broth", "stock", "sauce", "dip", "dressing", "marinade",
    "gravy", "condiment", "topping", "garnish", "seasoning", "spice", "herb",
    "beverage", "drink", "appetizer", "entree", "dessert", "side",
    "dish", "meal", "snack", "food", "platter", "combo", "special", "starter",
}


def normalize(name: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace, strip a trailing 's'."""
    s = _STRIP.sub(" ", name.lower())
    s = re.sub(r"\s+", " ", s).strip()
    if s.endswith("s") and len(s) > 3:
        s = s[:-1]
    return s


def is_generic_food_name(name: str) -> bool:
    """True when `name` is too broad to safely learn or match on: a bare
    category word ("soup", "sauce"), or a short phrase that ends in one
    ("dipping sauce", "tomato soup"). A longer, more specific phrase
    ("hot and sour soup") is fine."""
    normalized = normalize(name)
    if not normalized:
        return True
    words = normalized.split()
    if normalized in _GENERIC_FOOD_WORDS:
        return True
    if len(words) <= 2 and words[-1] in _GENERIC_FOOD_WORDS:
        return True
    return False


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


# Phrases in an LLM-supplied portion string that mean "noticeably more / less
# than one normal serving". Only the roll-up verdict uses these — never the
# per-item category shown on a chip.
_LARGE_PORTION = re.compile(
    r"\b(several|multiple|many|lots?|plenty|loads|pitchers?|carafes?|jugs?|"
    r"large|x-?large|extra[- ]large|jumbo|giant|huge|oversized|double|triple|"
    r"three|four|five|six|seven|eight|nine|ten|dozen)\b|"
    r"(?<!\d)([3-9]|\d{2,})\s*(?:x\b|cans?\b|bottles?\b|glasses\b|pints?\b|"
    r"servings?\b|slices?\b|pieces?\b|drinks?\b|beers?\b|plates?\b|bowls?\b|"
    r"cups?\b|helpings?\b|portions?\b|scoops?\b|shots?\b)"
)
_SMALL_PORTION = re.compile(
    r"\b(a sip|a bite|a taste|a nibble|small|half|mini|tiny|"
    r"single|a single|one|a little|a bit)\b"
)


def portion_size(text: str) -> str | None:
    """Classify a free-text portion ("6 cans", "a single glass", "a plate") as
    "large", "small", or None when it's an ordinary / unstated serving. When a
    string reads as both, "large" wins (the safer call for gout)."""
    t = (text or "").strip().lower()
    if not t:
        return None
    if _LARGE_PORTION.search(t):
        return "large"
    if _SMALL_PORTION.search(t):
        return "small"
    return None


def _effective_category(category: str, size: str | None) -> str:
    """The category a portion-adjusted item contributes to the roll-up. A large
    serving of a moderate-purine food counts as high-risk; a small serving of a
    trigger counts one notch lower — but never erases the flag entirely."""
    if size == "large" and category == "limit":
        return "avoid"
    if size == "small":
        if category == "avoid":
            return "limit"
        if category == "limit":
            return "ok"
    return category


def overall_verdict(matched: list[dict], detected: list[dict] | None = None) -> str:
    """Roll per-item categories into a single scan verdict. When `detected` is
    given, each item's stated portion nudges its contribution up or down (see
    `_effective_category`); the per-item categories in `matched` are untouched.
    """
    if not matched:
        return "no_food"
    sizes = {
        (d.get("name") or "").strip(): portion_size(d.get("portion", ""))
        for d in (detected or [])
        if isinstance(d, dict)
    }
    cats = {
        _effective_category(m["category"], sizes.get(m["item"]))
        for m in matched
    }
    if "avoid" in cats:
        return "avoid"
    if "limit" in cats:
        return "caution"
    if cats and cats == {"unknown"}:
        return "caution"  # food present but nothing matched the list
    return "safe"
