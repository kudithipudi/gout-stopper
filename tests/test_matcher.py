from app.services.matcher import (
    is_generic_food_name,
    match_detected,
    overall_verdict,
    portion_size,
)


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


def test_generic_food_name_is_rejected():
    # A bare generic word, or a short phrase that ends in one, is too broad to
    # learn — "soup" would then flag every soup, "dipping sauce" every sauce.
    for name in ["soup", "Soup", "sauce", "herbs", "spices", "dipping sauce",
                 "iced beverage", "side dish", "tomato soup"]:
        assert is_generic_food_name(name), name


def test_specific_food_name_is_allowed():
    for name in ["natto", "hot and sour soup", "escargot", "kimchi",
                 "chicken tikka masala", "red pepper flakes"]:
        assert not is_generic_food_name(name), name


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


# --- portion-aware verdict ----------------------------------------------------


def test_portion_size_classification():
    assert portion_size("6 cans") == "large"
    assert portion_size("several glasses") == "large"
    assert portion_size("a pitcher") == "large"
    assert portion_size("a dozen") == "large"
    assert portion_size("a single glass") == "small"
    assert portion_size("a small bowl") == "small"
    assert portion_size("a sip") == "small"
    assert portion_size("") is None
    assert portion_size("a plate") is None


def test_large_portion_of_a_limit_food_escalates_the_verdict():
    matched = _match(["salmon"])  # salmon = limit -> "caution" on its own
    assert overall_verdict(matched) == "caution"
    detected = [{"name": "salmon", "portion": "a huge double portion"}]
    assert overall_verdict(matched, detected) == "avoid"


def test_small_portion_softens_an_avoid_verdict_to_caution():
    matched = _match(["beer"])  # beer = avoid
    assert overall_verdict(matched) == "avoid"
    detected = [{"name": "beer", "portion": "a single small glass"}]
    assert overall_verdict(matched, detected) == "caution"


def test_large_portion_never_escalates_a_low_purine_food():
    matched = _match(["eggs"])  # ok
    detected = [{"name": "eggs", "portion": "a dozen"}]
    assert overall_verdict(matched, detected) == "safe"


def test_portion_does_not_change_the_per_item_category():
    matched = _match(["beer"])
    detected = [{"name": "beer", "portion": "a tiny sip"}]
    overall_verdict(matched, detected)
    assert matched[0]["category"] == "avoid"  # the chip still says avoid
