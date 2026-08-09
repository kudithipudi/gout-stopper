"""Single LLM client for GoutStopper. All OpenRouter calls go through this module.

Three purposes, each with its own configurable model:
- detect: gate — is there any food in the photo at all?
- identify: list the distinct foods shown, with confidence.
- advice: plain-language takeaway for a gout-prone person, given the DB match.

Prompts return strict JSON; callers parse defensively. Model, temperature, and
timeout come from config.
"""

import base64
import json
import logging
import re
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

_DETECT_SYSTEM = """You look at photographs. Decide whether the photo contains any food.
"Food" means anything edible — a full meal, snacks, fruit, drinks, or a single ingredient.
A packaged food item or a restaurant plate counts. People, scenery, objects, animals, or an
empty table do NOT count.

Return ONLY a JSON object: {"has_food": true|false, "reason": "one short phrase"}"""

_IDENTIFY_SYSTEM = """You are a careful food identifier. Look at the photo and list every distinct
food or drink item that is clearly visible, using plain everyday names a shopper would recognize
(e.g. "grilled salmon", "white rice", "glass of beer", "apple", "french fries"). Prefer the
specific prepared dish when it is identifiable, but keep the list to genuinely distinct items.

Return ONLY a JSON object:
{"foods": [{"name": "plain food name", "confidence": 0.0-1.0}]}
Include no more than 12 items. If nothing is identifiable, return {"foods": []}."""

_ADVICE_SYSTEM = """You give friendly, brief, practical advice to someone who is prone to gout
attacks. You are given a list of detected foods and how each one rates for gout (avoid = high
purine, limit = moderate purine, ok = generally fine). Write 2-4 plain sentences: name the risky
items and suggest a simple swap or adjustment. Do not give medical instructions, prescribing, or
diagnoses, and never be preachy. Return ONLY a JSON object: {"advice": "the sentences",
"overall": "avoid"|"caution"|"safe"}"""


def _image_b64(raw: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _parse_json(content: str) -> Any:
    """OpenRouter JSON objects sometimes arrive wrapped in ```json fences."""
    content = content.strip()
    fenced = re.match(r"```(?:json)?\s*(.*?)\s*```", content, re.DOTALL)
    if fenced:
        content = fenced.group(1)
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        obj = content.find("{")
        arr = content.find("[")
        idx = min(i for i in (obj, arr) if i != -1) if (obj != -1 and arr != -1) else (obj if obj != -1 else arr)
        if idx != -1:
            try:
                return json.loads(content[idx:])
            except json.JSONDecodeError:
                pass
        logger.error("Could not parse LLM JSON output: %r", content[:500])
        return None


async def _chat_json(system: str, user_parts: list[dict], model: str) -> Any:
    settings = get_settings()
    if not settings.openrouter_api_key:
        logger.warning("No OPENROUTER_API_KEY set; skipping LLM call")
        return None
    payload = {
        "model": model,
        "temperature": settings.llm_temperature,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_parts},
        ],
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            resp.raise_for_status()
    except (httpx.HTTPStatusError, httpx.TransportError, httpx.TimeoutException) as exc:
        logger.error("OpenRouter request failed (%s): %s", model, exc)
        return None
    data = resp.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        logger.error("Unexpected OpenRouter response shape: %r", data)
        return None
    return _parse_json(content)


async def detect_food(raw: bytes, mime: str) -> dict | None:
    """Gate: is there any food in the photo? Returns {"has_food": bool, "reason": str}."""
    parts = [
        {"type": "text", "text": "Look at this photo. Does it contain any food?"},
        {"type": "image_url", "image_url": {"url": _image_b64(raw, mime)}},
    ]
    result = await _chat_json(_DETECT_SYSTEM, parts, get_settings().food_detect_model)
    if isinstance(result, dict) and "has_food" in result:
        return result
    return None


async def identify_foods(raw: bytes, mime: str) -> list[dict]:
    """List the distinct foods visible in the photo. Returns [{"name", "confidence"}...]."""
    parts = [
        {"type": "text", "text": "List every distinct food or drink item clearly visible in this photo."},
        {"type": "image_url", "image_url": {"url": _image_b64(raw, mime)}},
    ]
    return await _identify(parts)


async def identify_foods_from_text(text: str) -> list[dict] | None:
    """Parse a user-typed food description into distinct food items, the same
    shape as identify_foods(). Returns None when the LLM call itself failed
    (so callers can tell "LLM down" apart from "nothing identifiable")."""
    parts = [
        {
            "type": "text",
            "text": (
                'A user typed what they plan to eat: "%s". '
                "List every distinct food or drink item they mentioned, using plain everyday names."
                % text
            ),
        }
    ]
    result = await _chat_json(_IDENTIFY_SYSTEM, parts, get_settings().food_identify_model)
    if result is None:
        return None
    if isinstance(result, dict) and isinstance(result.get("foods"), list):
        return [f for f in result["foods"] if isinstance(f, dict) and f.get("name")]
    return []


async def _identify(parts: list[dict]) -> list[dict]:
    result = await _chat_json(_IDENTIFY_SYSTEM, parts, get_settings().food_identify_model)
    if isinstance(result, dict) and isinstance(result.get("foods"), list):
        return [f for f in result["foods"] if isinstance(f, dict) and f.get("name")]
    return []


async def generate_advice(detected: list[dict], matched: list[dict]) -> tuple[str, str]:
    """Friendly 2-4 sentence takeaway. Returns (advice_text, overall_verdict)."""
    settings = get_settings()
    lines = [
        f"- {m['item']}: {m['category']}"
        for m in matched
    ]
    user = (
        "Detected foods and gout ratings:\n"
        + ("\n".join(lines) if lines else "(none identified)")
        + "\n\nWrite a short, practical takeaway for a person prone to gout."
    )
    result = await _chat_json(_ADVICE_SYSTEM, [{"type": "text", "text": user}], settings.advice_model)
    if isinstance(result, dict):
        return str(result.get("advice", "")), str(result.get("overall", "safe"))
    return "", "safe"
