"""Single LLM client for GoutStopper. All OpenRouter calls go through this module.

Purposes, each with its own configurable model:
- analyze  (photo): one vision call — is there food, and what is it?
- identify (text):  parse a typed description into distinct food items.
- classify:         rate a food for gout risk when it isn't on any list.
- advice:           plain-language takeaway for a gout-prone person.

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

_ANALYZE_SYSTEM = """You look at photographs of food. Do two things in one pass:

1. Decide whether the photo contains any food at all. "Food" means anything
   edible — a full meal, snacks, fruit, drinks, or a single ingredient. A
   packaged item or a restaurant plate counts. People, scenery, objects,
   animals, or an empty table do NOT count.
2. If there is food, list every distinct food or drink item that is clearly
   visible, using plain everyday names a shopper would recognize (e.g. "grilled
   salmon", "white rice", "glass of beer", "french fries"). Prefer the specific
   prepared dish when identifiable. Keep the list to genuinely distinct items,
   no more than 12. When the portion is obvious, note it briefly.

Return ONLY a JSON object:
{"has_food": true|false, "reason": "one short phrase",
 "foods": [{"name": "plain food name", "confidence": 0.0-1.0, "portion": "e.g. 'a pint' or '' if unclear"}]}
If has_food is false, return "foods": []."""

_IDENTIFY_SYSTEM = """You are a careful food identifier. Given a short description of what someone
plans to eat, list every distinct food or drink item they mentioned, using plain everyday names a
shopper would recognize (e.g. "grilled salmon", "white rice", "glass of beer", "french fries").
Keep the list to genuinely distinct items. When the person stated a portion or quantity, capture
it briefly; otherwise leave it blank. Keep the item `name` free of quantities.

Return ONLY a JSON object:
{"foods": [{"name": "plain food name", "confidence": 0.0-1.0, "portion": "e.g. 'a pint' or ''"}]}
Include no more than 12 items. If nothing is identifiable, return {"foods": []}."""

_CLASSIFY_SYSTEM = """You rate individual foods and drinks for how much they tend to provoke gout,
based on purine content and known effects on uric acid. Use three ratings:
- "avoid": high-purine or strongly raises uric acid (organ meats, game, anchovies, beer, ...).
- "limit": moderate purine or mixed evidence (most fish, poultry, legumes, moderate alcohol, ...).
- "ok": low purine and generally fine (most vegetables, fruit, eggs, dairy, grains, coffee, ...).

You are given a list of food names. Return ONLY a JSON object:
{"ratings": [{"name": "<exact name from the input>", "category": "avoid"|"limit"|"ok",
              "reason": "at most 12 words"}]}
Rate every name you are given."""

_ADVICE_SYSTEM = """You give friendly, brief, practical advice to someone who is prone to gout
attacks. You are given a list of detected foods (sometimes with rough portions) and how each one
rates for gout (avoid = high purine, limit = moderate purine, ok = generally fine). Write 2-4
plain sentences: name the risky items and suggest a simple swap or adjustment, and where a portion
is given, factor it in (a single drink is lower risk than several). Do not give medical
instructions, prescribing, or diagnoses, and never be preachy. Return ONLY a JSON object:
{"advice": "the sentences", "overall": "avoid"|"caution"|"safe"}"""


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
    except (KeyError, IndexError):
        logger.error("Unexpected OpenRouter response shape: %r", data)
        return None
    return _parse_json(content)


def _clean_foods(raw_foods: Any) -> list[dict]:
    """Normalise a raw "foods" list from the model into
    [{"name": str, "confidence": float, "portion": str}]."""
    if not isinstance(raw_foods, list):
        return []
    out = []
    for f in raw_foods:
        if not isinstance(f, dict) or not f.get("name"):
            continue
        out.append(
            {
                "name": str(f["name"]).strip(),
                "confidence": round(float(f.get("confidence") or 0.5), 2),
                "portion": str(f.get("portion") or "").strip(),
            }
        )
    return out


async def analyze_photo(raw: bytes, mime: str) -> dict | None:
    """One vision call: gate + identify. Returns
    {"has_food": bool, "reason": str, "foods": [{"name","confidence","portion"}]}
    or None when the LLM call itself failed."""
    parts = [
        {"type": "text", "text": "Analyze this photo: is there food, and what food?"},
        {"type": "image_url", "image_url": {"url": _image_b64(raw, mime)}},
    ]
    result = await _chat_json(_ANALYZE_SYSTEM, parts, get_settings().food_detect_model)
    if not isinstance(result, dict) or "has_food" not in result:
        return None
    return {
        "has_food": bool(result.get("has_food")),
        "reason": str(result.get("reason") or ""),
        "foods": _clean_foods(result.get("foods")),
    }


async def identify_foods_from_text(text: str) -> list[dict] | None:
    """Parse a user-typed food description into distinct food items, the same
    shape as analyze_photo()["foods"]. Returns None when the LLM call itself
    failed (so callers can tell "LLM down" apart from "nothing identifiable")."""
    parts = [
        {
            "type": "text",
            "text": (
                'A user typed what they plan to eat: "%s". '
                "List every distinct food or drink item they mentioned." % text
            ),
        }
    ]
    result = await _chat_json(_IDENTIFY_SYSTEM, parts, get_settings().food_identify_model)
    if result is None:
        return None
    if isinstance(result, dict):
        return _clean_foods(result.get("foods"))
    return []


async def classify_gout_risk(names: list[str]) -> dict[str, dict]:
    """Rate each food name for gout risk. Returns
    { name: {"category": "avoid"|"limit"|"ok", "reason": str} } keyed by the
    original (pre-normalisation) name. Empty dict on failure or when disabled."""
    names = [n for n in (s.strip() for s in names) if n]
    if not names:
        return {}
    settings = get_settings()
    if not settings.gout_classify_enabled:
        return {}
    user = "Rate these foods for gout risk:\n" + "\n".join(f"- {n}" for n in names)
    result = await _chat_json(
        _CLASSIFY_SYSTEM, [{"type": "text", "text": user}], settings.gout_classify_model
    )
    if not isinstance(result, dict) or not isinstance(result.get("ratings"), list):
        return {}
    valid = {"avoid", "limit", "ok"}
    by_name = {n.lower(): n for n in names}
    out: dict[str, dict] = {}
    for r in result["ratings"]:
        if not isinstance(r, dict):
            continue
        cat = str(r.get("category") or "").strip().lower()
        rated = str(r.get("name") or "").strip()
        if cat not in valid or not rated:
            continue
        key = by_name.get(rated.lower(), rated)
        out[key] = {"category": cat, "reason": str(r.get("reason") or "").strip()}
    return out


async def generate_advice(detected: list[dict], matched: list[dict]) -> tuple[str, str]:
    """Friendly 2-4 sentence takeaway. Returns (advice_text, overall_verdict)."""
    settings = get_settings()
    portions = {d.get("name"): d.get("portion") for d in detected if isinstance(d, dict)}
    lines = []
    for m in matched:
        portion = (portions.get(m["item"]) or "").strip()
        suffix = f" ({portion})" if portion else ""
        lines.append(f"- {m['item']}{suffix}: {m['category']}")
    user = (
        "Detected foods and gout ratings:\n"
        + ("\n".join(lines) if lines else "(none identified)")
        + "\n\nWrite a short, practical takeaway for a person prone to gout."
    )
    result = await _chat_json(_ADVICE_SYSTEM, [{"type": "text", "text": user}], settings.advice_model)
    if isinstance(result, dict):
        return str(result.get("advice", "")), str(result.get("overall", "safe"))
    return "", "safe"
