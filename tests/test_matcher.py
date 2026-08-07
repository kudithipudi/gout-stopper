from app.services.matcher import match_detected, overall_verdict


def _food(name, category, aliases=""):
    return {"name": name, "category": category, "aliases": aliases}


SAMPLE = [
    _food("beer", "avoid", "lager, ale"),
    _food("red meat", "avoid", "beef, lamb, steak"),
    _food("shrimp", "avoid", "prawns, prawn"),
    _food("salmon", "limit", "salmon"),
    _food("lentils", "limit", "dal"),
    _food("eggs", "ok", "egg"),
    _food("cherries", "ok", "cherry"),
]


def _match(items):
    return match_detected([{"name": n} for n in items], SAMPLE)


def test_exact_match():
    assert _match(["beer"])[0]["category"] == "avoid"


def test_plural_exact_match():
    assert _match(["beers"])[0]["category"] == "avoid"


def test_alias_match():
    assert _match(["prawn"])[0]["category"] == "avoid"
    assert _match(["prawns"])[0]["category"] == "avoid"


def test_substring_match():
    assert _match(["grilled salmon"])[0]["category"] == "limit"


def test_unknown_stays_unknown():
    assert _match(["kimchi"])[0]["category"] == "unknown"


def test_avoid_wins_over_ok():
    # "beer" appears in nothing else, but exercise priority explicitly with a
    # detected item that touches both lists.
    mixed = SAMPLE + [_food("malt", "ok", "beer malt")]
    result = match_detected([{"name": "beer"}], mixed)[0]
    assert result["category"] == "avoid"


def test_verdicts():
    avoid = _match(["beer", "eggs"])
    assert overall_verdict(avoid) == "avoid"

    caution = _match(["salmon"])
    assert overall_verdict(caution) == "caution"

    safe = _match(["eggs", "cherries"])
    assert overall_verdict(safe) == "safe"

    unknown = _match(["kimchi"])
    assert overall_verdict(unknown) == "caution"

    assert overall_verdict([]) == "no_food"
