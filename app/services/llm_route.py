"""Where an OpenAI-style chat call goes: OpenRouter, or Straitly when enabled.

Straitly (api.straitly.ai) speaks the OpenRouter API and was 25-45% cheaper on
the models below when measured on 2026-11-10. Set LLM_PROVIDER=straitly and
STRAITLY_API_KEY to send those models there. Every other model stays on
OpenRouter, and a failed Straitly call is retried on OpenRouter.
"""

from dataclasses import dataclass

OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
STRAITLY_CHAT_URL = "https://api.straitly.ai/v1/chat/completions"

# OpenRouter model id -> Straitly model id. Only models Straitly carries and is
# cheaper for. Left out on purpose: z-ai/glm-5.3-flash (output ~60% pricier
# there), and models Straitly doesn't carry (mercury-3, gpt-audio, image models).
STRAITLY_MODELS = {
    "google/gemini-3.1-flash-lite": "google/gemini-3.1-flash-lite",
    "google/gemini-3-flash-preview": "google/gemini-3-flash-preview",
    "google/gemini-2.5-flash-lite": "google/gemini-2.5-flash-lite",
    "google/gemini-3.8-flash": "gemini-3.8-flash",
    "anthropic/claude-haiku-5.5": "claude-haiku-5-5",
    "openai/gpt-4o-mini": "openai/gpt-4o-mini",
    "openai/gpt-4o": "openai/gpt-4o",
    "meta-llama/llama-3.3-70b-instruct": "meta-llama/llama-3.3-70b-instruct",
}


@dataclass(frozen=True)
class Route:
    name: str  # "straitly" or "openrouter", for logs
    url: str
    api_key: str
    model: str  # the model id as this provider spells it


def chat_routes(
    model: str,
    *,
    openrouter_key: str,
    provider: str = "openrouter",
    straitly_key: str = "",
) -> list[Route]:
    """Providers to try, in order. Empty when no key is set at all."""
    routes = []
    if (provider or "").strip().lower() == "straitly" and straitly_key and model in STRAITLY_MODELS:
        routes.append(Route("straitly", STRAITLY_CHAT_URL, straitly_key, STRAITLY_MODELS[model]))
    if openrouter_key:
        routes.append(Route("openrouter", OPENROUTER_CHAT_URL, openrouter_key, model))
    return routes
