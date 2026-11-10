from app.services.llm_route import OPENROUTER_CHAT_URL, STRAITLY_CHAT_URL, chat_routes


def test_default_is_openrouter_only():
    routes = chat_routes("google/gemini-3.1-flash-lite", openrouter_key="or", straitly_key="st")
    assert [(r.name, r.url, r.model) for r in routes] == [
        ("openrouter", OPENROUTER_CHAT_URL, "google/gemini-3.1-flash-lite")
    ]


def test_straitly_first_with_openrouter_fallback_and_renamed_model():
    routes = chat_routes(
        "anthropic/claude-haiku-5.5", openrouter_key="or", provider="Straitly", straitly_key="st"
    )
    assert [(r.name, r.url, r.api_key, r.model) for r in routes] == [
        ("straitly", STRAITLY_CHAT_URL, "st", "claude-haiku-5-5"),
        ("openrouter", OPENROUTER_CHAT_URL, "or", "anthropic/claude-haiku-5.5"),
    ]


def test_unlisted_model_or_missing_key_stays_on_openrouter():
    for model, key in (("z-ai/glm-5.3-flash", "st"), ("google/gemini-3.1-flash-lite", "")):
        routes = chat_routes(model, openrouter_key="or", provider="straitly", straitly_key=key)
        assert [r.name for r in routes] == ["openrouter"]


def test_no_keys_means_no_routes():
    assert chat_routes("google/gemini-3.1-flash-lite", openrouter_key="") == []
