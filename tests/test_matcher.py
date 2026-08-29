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


def test_shared_single_word_is_not_a_match():
    """Regression: "white rice" was rated as wine and "grilled chicken thigh"
    as broth because they shared one common word with an alias."""
    real = [
        _food("wine", "limit", "red wine, white wine"),
        _food("rice", "ok", "rice, white rice, brown rice"),
        _food("broth", "avoid", "bone broth, beef broth, chicken stock"),
        _food("chicken", "limit", "poultry, chicken breast"),
    ]
    by_name = {r["item"]: r for r in match_detected(
        [{"name": "white rice"}, {"name": "grilled chicken thigh"}], real
    )}
    assert by_name["white rice"]["category"] == "ok"
    assert by_name["grilled chicken thigh"]["category"] == "limit"

    # ...but a real phrase match still lands:
    assert match_detected([{"name": "chicken broth"}], real)[0]["category"] == "avoid"


def test_source_is_tagged():
    matched = _match(["beer"])[0]
    assert matched["source"] == "list"

    unknown = _match(["kimchi"])[0]
    assert unknown["source"] == "unknown"

    learned = match_detected(
        [{"name": "kimchi"}], [_food("kimchi", "limit")], source="learned"
    )[0]
    assert learned["category"] == "limit"
    assert learned["source"] == "learned"


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
