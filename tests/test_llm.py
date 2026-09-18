"""Unit tests for the LLM service layer (app/services/llm.py).

The network boundary is always mocked — no test here makes a real HTTP call.
Two seams are used, mirroring worksheets' tests/test_llm.py and
tests/test_review.py:

- `_chat_json` itself is tested by faking `_get_client()` with a tiny stub
  whose `post()` returns a canned response (or raises), the same shape as
  worksheets' `_FakeClient`/`_FakeResp`.
- The higher-level functions (analyze_photo, identify_foods_from_text,
  classify_gout_risk, generate_advice) are tested by faking `_chat_json`
  itself, so orchestration/parsing logic is exercised without re-mocking HTTP
  mechanics.

Note: conftest.py's autouse `_no_llm_network` fixture monkeypatches the
module attribute `app.services.llm._chat_json` to a network-blocking stub for
every test. `_REAL_CHAT_JSON` below is captured at import time (before that
per-test patch is installed), so it still refers to the genuine function and
lets the `_chat_json`-internals tests below exercise the real code while
still never touching the network (its `_get_client` global lookup resolves
through the module namespace, which these tests patch instead).
"""

import types

import httpx
import pytest

from app.services import llm

_REAL_CHAT_JSON = llm._chat_json


def _settings(**overrides):
    base = dict(
        openrouter_api_key="test-key",
        llm_temperature=0.0,
        llm_timeout=5,
        food_detect_model="detect-model",
        food_identify_model="identify-model",
        gout_classify_model="classify-model",
        gout_classify_enabled=True,
        advice_model="advice-model",
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _fake_returning(value):
    async def _fake(*args, **kwargs):
        return value

    return _fake


class _FakeResponse:
    def __init__(self, json_data=None, status_error=None):
        self._json_data = json_data
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error:
            raise self._status_error

    def json(self):
        return self._json_data


class _FakeClient:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc
        self.calls = []

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self._exc:
            raise self._exc
        return self._response


def _ok_response(content, usage=None):
    data = {"choices": [{"message": {"content": content}}]}
    if usage:
        data["usage"] = usage
    return _FakeResponse(json_data=data)


@pytest.fixture(autouse=True)
async def _reset_llm_client():
    """Guard against a leaked pooled client across the client-lifecycle
    tests below, regardless of test order."""
    await llm.close_client()
    yield
    await llm.close_client()


class TestImageB64:
    def test_data_uri_shape(self):
        uri = llm._image_b64(b"hello", "image/jpeg")
        assert uri.startswith("data:image/jpeg;base64,")
        import base64

        assert base64.b64decode(uri.split(",", 1)[1]) == b"hello"


class TestParseJson:
    def test_plain_object(self):
        assert llm._parse_json('{"a": 1}') == {"a": 1}

    def test_code_fenced_json(self):
        assert llm._parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_code_fenced_no_lang(self):
        assert llm._parse_json('```\n{"a": 1}\n```') == {"a": 1}

    def test_object_after_leading_prose(self):
        assert llm._parse_json('Here is the result: {"a": 1}') == {"a": 1}

    def test_array_after_leading_prose(self):
        assert llm._parse_json("Here is the result: [1, 2, 3]") == [1, 2, 3]

    def test_picks_earlier_marker_for_nested_structure(self):
        # The outer "{" precedes the nested "[" — the earlier-index branch
        # must be picked, and the whole (valid) object parsed from there.
        assert llm._parse_json('Model says: {"a": [1, 2, 3]}') == {"a": [1, 2, 3]}

    def test_unparseable_returns_none(self):
        assert llm._parse_json("not json at all, no braces or brackets") is None

    def test_broken_braces_returns_none(self):
        assert llm._parse_json("{not: valid, json}") is None


class TestClientLifecycle:
    async def test_get_client_creates_and_reuses_same_instance(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        c1 = llm._get_client()
        c2 = llm._get_client()
        assert c1 is c2
        assert isinstance(c1, httpx.AsyncClient)

    async def test_get_client_replaces_closed_client(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        c1 = llm._get_client()
        await c1.aclose()
        c2 = llm._get_client()
        assert c2 is not c1
        assert not c2.is_closed

    async def test_close_client_closes_and_resets_global(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        c1 = llm._get_client()
        await llm.close_client()
        assert c1.is_closed
        assert llm._client is None

    async def test_close_client_is_a_noop_when_nothing_open(self):
        await llm.close_client()  # must not raise
        assert llm._client is None


class TestChatJsonInternals:
    """Exercises the real `_chat_json` (captured before conftest's autouse
    fixture patches it out) with a fake client standing in for the network."""

    async def test_no_api_key_returns_none_without_calling_client(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings(openrouter_api_key=""))
        fake = _FakeClient()
        monkeypatch.setattr(llm, "_get_client", lambda: fake)
        result = await _REAL_CHAT_JSON("sys", [{"type": "text", "text": "hi"}], "model-x")
        assert result is None
        assert fake.calls == []

    async def test_success_returns_parsed_json_and_sends_expected_payload(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        fake = _FakeClient(response=_ok_response('{"has_food": true}'))
        monkeypatch.setattr(llm, "_get_client", lambda: fake)
        result = await _REAL_CHAT_JSON("sys", [{"type": "text", "text": "hi"}], "model-x", purpose="analyze")
        assert result == {"has_food": True}
        assert fake.calls[0]["json"]["model"] == "model-x"
        assert fake.calls[0]["json"]["response_format"] == {"type": "json_object"}
        assert fake.calls[0]["headers"]["Authorization"] == "Bearer test-key"

    async def test_logs_and_returns_parsed_json_when_reasoning_tokens_present(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        resp = _ok_response(
            '{"a": 1}',
            usage={
                "prompt_tokens": 10,
                "completion_tokens": 5,
                "completion_tokens_details": {"reasoning_tokens": 3},
            },
        )
        fake = _FakeClient(response=resp)
        monkeypatch.setattr(llm, "_get_client", lambda: fake)
        result = await _REAL_CHAT_JSON("sys", [], "model-x")
        assert result == {"a": 1}

    async def test_http_status_error_returns_none(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        request = httpx.Request("POST", llm._OPENROUTER_URL)
        response = httpx.Response(500, request=request)
        resp = _FakeResponse(status_error=httpx.HTTPStatusError("boom", request=request, response=response))
        fake = _FakeClient(response=resp)
        monkeypatch.setattr(llm, "_get_client", lambda: fake)
        assert await _REAL_CHAT_JSON("sys", [], "model-x") is None

    async def test_transport_error_returns_none(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        fake = _FakeClient(exc=httpx.ConnectError("no route"))
        monkeypatch.setattr(llm, "_get_client", lambda: fake)
        assert await _REAL_CHAT_JSON("sys", [], "model-x") is None

    async def test_timeout_returns_none(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        fake = _FakeClient(exc=httpx.ReadTimeout("too slow"))
        monkeypatch.setattr(llm, "_get_client", lambda: fake)
        assert await _REAL_CHAT_JSON("sys", [], "model-x") is None

    async def test_missing_choices_key_returns_none(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        fake = _FakeClient(response=_FakeResponse(json_data={"unexpected": "shape"}))
        monkeypatch.setattr(llm, "_get_client", lambda: fake)
        assert await _REAL_CHAT_JSON("sys", [], "model-x") is None

    async def test_empty_choices_list_returns_none(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        fake = _FakeClient(response=_FakeResponse(json_data={"choices": []}))
        monkeypatch.setattr(llm, "_get_client", lambda: fake)
        assert await _REAL_CHAT_JSON("sys", [], "model-x") is None


class TestCleanFoods:
    def test_not_a_list_returns_empty(self):
        assert llm._clean_foods("not a list") == []
        assert llm._clean_foods(None) == []

    def test_filters_items_without_a_usable_name(self):
        raw = [{"name": "rice"}, {"confidence": 0.9}, "not a dict", {"name": ""}]
        assert llm._clean_foods(raw) == [{"name": "rice", "confidence": 0.5, "portion": ""}]

    def test_normalizes_confidence_and_strips_whitespace(self):
        raw = [{"name": " Beer ", "confidence": "0.876", "portion": " a pint "}]
        assert llm._clean_foods(raw) == [{"name": "Beer", "confidence": 0.88, "portion": "a pint"}]

    def test_default_confidence_when_missing_or_none(self):
        assert llm._clean_foods([{"name": "Rice", "confidence": None}])[0]["confidence"] == 0.5


class TestAnalyzePhoto:
    async def test_none_when_chat_json_fails(self, monkeypatch):
        monkeypatch.setattr(llm, "_chat_json", _fake_returning(None))
        assert await llm.analyze_photo(b"data", "image/jpeg") is None

    async def test_none_when_result_not_a_dict(self, monkeypatch):
        monkeypatch.setattr(llm, "_chat_json", _fake_returning(["not", "a", "dict"]))
        assert await llm.analyze_photo(b"data", "image/jpeg") is None

    async def test_none_when_has_food_key_missing(self, monkeypatch):
        monkeypatch.setattr(llm, "_chat_json", _fake_returning({"reason": "x"}))
        assert await llm.analyze_photo(b"data", "image/jpeg") is None

    async def test_normalizes_successful_result_and_builds_image_part(self, monkeypatch):
        captured = {}

        async def fake_chat_json(system, parts, model, *, purpose="chat"):
            captured["parts"] = parts
            captured["purpose"] = purpose
            return {"has_food": True, "reason": "clear plate", "foods": [{"name": "Rice"}]}

        monkeypatch.setattr(llm, "_chat_json", fake_chat_json)
        result = await llm.analyze_photo(b"rawbytes", "image/png")
        assert result == {
            "has_food": True,
            "reason": "clear plate",
            "foods": [{"name": "Rice", "confidence": 0.5, "portion": ""}],
        }
        assert captured["purpose"] == "analyze"
        assert captured["parts"][1]["image_url"]["url"].startswith("data:image/png;base64,")


class TestIdentifyFoodsFromText:
    async def test_none_when_chat_json_fails(self, monkeypatch):
        monkeypatch.setattr(llm, "_chat_json", _fake_returning(None))
        assert await llm.identify_foods_from_text("beer and fries") is None

    async def test_dict_result_is_cleaned(self, monkeypatch):
        monkeypatch.setattr(llm, "_chat_json", _fake_returning({"foods": [{"name": "Fries"}]}))
        result = await llm.identify_foods_from_text("fries")
        assert result == [{"name": "Fries", "confidence": 0.5, "portion": ""}]

    async def test_non_dict_result_returns_empty_list(self, monkeypatch):
        monkeypatch.setattr(llm, "_chat_json", _fake_returning(["oops"]))
        assert await llm.identify_foods_from_text("fries") == []

    async def test_user_text_is_embedded_in_the_prompt(self, monkeypatch):
        captured = {}

        async def fake(system, parts, model, *, purpose="chat"):
            captured["text"] = parts[0]["text"]
            return {"foods": []}

        monkeypatch.setattr(llm, "_chat_json", fake)
        await llm.identify_foods_from_text("shrimp tacos")
        assert "shrimp tacos" in captured["text"]


class TestClassifyGoutRisk:
    async def test_empty_names_short_circuits_without_calling(self, monkeypatch):
        called = False

        async def fake(*a, **k):
            nonlocal called
            called = True
            return {}

        monkeypatch.setattr(llm, "_chat_json", fake)
        assert await llm.classify_gout_risk(["  ", ""]) == {}
        assert called is False

    async def test_disabled_returns_empty_without_calling(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings(gout_classify_enabled=False))
        called = False

        async def fake(*a, **k):
            nonlocal called
            called = True
            return {}

        monkeypatch.setattr(llm, "_chat_json", fake)
        assert await llm.classify_gout_risk(["beer"]) == {}
        assert called is False

    async def test_invalid_result_shape_returns_empty(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        monkeypatch.setattr(llm, "_chat_json", _fake_returning({"not_ratings": []}))
        assert await llm.classify_gout_risk(["beer"]) == {}

    async def test_valid_ratings_mapped_back_to_original_names(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())

        async def fake(system, parts, model, *, purpose="chat"):
            return {
                "ratings": [
                    {"name": "BEER", "category": "AVOID", "reason": "purine bomb"},
                    {"name": "unknown food", "category": "bogus", "reason": "n/a"},
                    "not a dict",
                ]
            }

        monkeypatch.setattr(llm, "_chat_json", fake)
        result = await llm.classify_gout_risk([" Beer ", "Rice"])
        assert result == {"Beer": {"category": "avoid", "reason": "purine bomb"}}


class TestGenerateAdvice:
    async def test_dict_result_returns_advice_and_overall(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        monkeypatch.setattr(
            llm, "_chat_json", _fake_returning({"advice": "Swap the beer for water.", "overall": "caution"})
        )
        advice, overall = await llm.generate_advice([], [])
        assert advice == "Swap the beer for water."
        assert overall == "caution"

    async def test_non_dict_result_defaults_to_safe(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        monkeypatch.setattr(llm, "_chat_json", _fake_returning(None))
        advice, overall = await llm.generate_advice([], [])
        assert advice == ""
        assert overall == "safe"

    async def test_prompt_includes_portion_when_present(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        captured = {}

        async def fake(system, parts, model, *, purpose="chat"):
            captured["text"] = parts[0]["text"]
            return {"advice": "", "overall": "safe"}

        monkeypatch.setattr(llm, "_chat_json", fake)
        detected = [{"name": "Beer", "portion": "a pint"}]
        matched = [{"item": "Beer", "category": "avoid"}]
        await llm.generate_advice(detected, matched)
        assert "Beer (a pint): avoid" in captured["text"]

    async def test_prompt_notes_none_identified_when_no_matches(self, monkeypatch):
        monkeypatch.setattr(llm, "get_settings", lambda: _settings())
        captured = {}

        async def fake(system, parts, model, *, purpose="chat"):
            captured["text"] = parts[0]["text"]
            return {"advice": "", "overall": "safe"}

        monkeypatch.setattr(llm, "_chat_json", fake)
        await llm.generate_advice([], [])
        assert "(none identified)" in captured["text"]
