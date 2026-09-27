"""LLM providers returning JSON validated against a Pydantic model, chained per role (config llm.creative /
llm.checker): hosted models first when a key is set, local Ollama as the $0 fallback."""

import copy
import json
import logging
import time
from typing import Callable, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from vidgen.config import Settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str
    tier: str   # "strong" (hosted, large) or "base" (local 8B): picks the prompt variant (templates.render)

    def generate_json(self, prompt: str, schema: type[BaseModel]) -> str: ...


class GeminiProvider:
    tier = "strong"

    def __init__(self, api_key: str, model: str):
        self.name = f"gemini:{model}"
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def generate_json(self, prompt: str, schema: type[BaseModel]) -> str:
        from google.genai import types

        resp = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=schema.model_json_schema(),
                temperature=0.8,
            ),
        )
        return resp.text or ""


def strict_schema(schema: type[BaseModel]) -> dict:
    """The JSON schema in OpenAI strict form: every object closed and every property required (fields with a
    default just get filled in by the model), no "default" keys."""
    out = copy.deepcopy(schema.model_json_schema())

    def close(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"])
                for prop in node["properties"].values():
                    prop.pop("default", None)
            for v in node.values():
                close(v)
        elif isinstance(node, list):
            for v in node:
                close(v)
    close(out)
    return out


class GroqProvider:
    """Groq's OpenAI-compatible API (free tier: ~1000 requests/day, 8000 tokens/minute per model). A short
    wait advised by 429 is honoured; a long one (daily cap) hands over to the next provider."""
    tier = "strong"
    URL = "https://api.groq.com/openai/v1/chat/completions"
    RATE_WAIT_MAX = 65.0
    RATE_WAITS = 2

    def __init__(self, api_key: str, model: str, sleep: Callable[[float], None] | None = None):
        self.name = f"groq:{model}"
        self._key = api_key
        self._model = model
        self._sleep = sleep or time.sleep

    def _post(self, prompt: str, response_format: dict) -> httpx.Response:
        body = {"model": self._model, "messages": [{"role": "user", "content": prompt}],
                "response_format": response_format, "temperature": 0.7}
        if self._model.startswith("openai/gpt-oss"):
            body["reasoning_effort"] = "low"   # reasoning tokens count against the per-minute budget
        for n in range(self.RATE_WAITS + 1):
            resp = httpx.post(self.URL, json=body, headers={"Authorization": f"Bearer {self._key}"}, timeout=120)
            if resp.status_code != 429:
                return resp
            wait = float(resp.headers.get("retry-after") or self.RATE_WAIT_MAX + 1)
            if wait > self.RATE_WAIT_MAX or n == self.RATE_WAITS:
                raise LLMError(f"Groq rate limit ({self._model}), retry in {wait:.0f}s")
            log.info("groq rate limit, waiting %.0fs", wait)
            self._sleep(wait)
        raise AssertionError("unreachable")

    def generate_json(self, prompt: str, schema: type[BaseModel]) -> str:
        resp = self._post(prompt, {"type": "json_schema", "json_schema": {
            "name": schema.__name__, "strict": True, "schema": strict_schema(schema)}})
        if resp.status_code == 400:   # a schema shape the model's strict mode doesn't take: plain JSON mode
            log.warning("groq %s rejected the schema, using json_object mode", self._model)
            resp = self._post(f"{prompt}\n\nAnswer with one JSON object matching this JSON schema:\n"
                              f"{json.dumps(schema.model_json_schema())}", {"type": "json_object"})
        if resp.status_code in (401, 403):
            raise LLMError("Groq API key invalid or not allowed (GROQ_API_KEY in .env)")
        if resp.status_code != 200:
            raise LLMError(f"Groq {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]["content"]


class OllamaProvider:
    tier = "base"

    def __init__(self, url: str, model: str, think: bool = False):
        self.name = f"ollama:{model}"
        self._url = url.rstrip("/")
        self._model = model
        self._think = think

    def generate_json(self, prompt: str, schema: type[BaseModel]) -> str:
        resp = httpx.post(
            f"{self._url}/api/chat",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "format": schema.model_json_schema(),
                "think": self._think,
                "stream": False,
                # 8k context: prompts are <3k tokens; 16k would push an 8B model past 8GB VRAM
                "options": {"temperature": 0.5, "num_ctx": 8192},  # lower = fewer "creative" factual slips
            },
            timeout=900,
        )
        if resp.status_code != 200:
            detail = resp.json().get("error", resp.text) if resp.headers.get("content-type", "").startswith(
                "application/json") else resp.text
            hint = f" — run: ollama pull {self._model}" if "not found" in str(detail) else ""
            raise LLMError(f"Ollama {resp.status_code}: {detail}{hint}")
        return resp.json()["message"]["content"]


class LLMChain:
    """Tries providers in order; each gets 1 + `retries` attempts to return schema-valid JSON."""

    def __init__(self, providers: list[LLMProvider], retries: int = 2):
        if not providers:
            raise LLMError("No LLM provider configured: check llm.providers in config.yaml")
        self.providers = providers
        self.retries = retries
        self.last_provider = ""   # who answered the last generate() ("groq:openai/gpt-oss-120b", ...)
        self.fallback = False     # True when that was not the first provider
        self.used: list[str] = []  # every answer's provider, for usage()

    def generate(self, prompt: str | Callable[[str], str], schema: type[T]) -> T:
        """`prompt` may be a function of the provider's tier (templates.prompt): each provider gets its own."""
        errors: list[str] = []
        for provider in self.providers:
            text = prompt(getattr(provider, "tier", "base")) if callable(prompt) else prompt
            for attempt in range(1, self.retries + 2):
                try:
                    raw = provider.generate_json(text, schema)
                    out = schema.model_validate(json.loads(raw))
                    self.last_provider, self.fallback = provider.name, provider is not self.providers[0]
                    self.used.append(provider.name)
                    return out
                except (json.JSONDecodeError, ValidationError) as e:
                    errors.append(f"{provider.name}#{attempt}: invalid output: {e}")
                    log.warning("%s returned invalid JSON (attempt %d)", provider.name, attempt)
                except Exception as e:  # network, quota, auth → next provider
                    errors.append(f"{provider.name}: {type(e).__name__}: {e}")
                    log.warning("%s failed: %s", provider.name, e)
                    break
        raise LLMError("All LLM providers failed:\n" + "\n".join(errors))


    def usage(self) -> dict:
        """Which models answered so far, and whether any answer came from a fallback (shown in the studio)."""
        models = list(dict.fromkeys(self.used))
        return {"models": models, "fallback": any(m != self.providers[0].name for m in models)}


def default_chain(s: Settings, role: str = "creative") -> LLMChain:
    """The role's providers in config order (`llm.creative` / `llm.checker`); hosted ones without a key are
    skipped, so with no keys this is the local Ollama chain."""
    cfg = s.pipeline.llm
    providers: list[LLMProvider] = []
    for spec in getattr(cfg, role):
        name, _, model = spec.partition(":")
        if name == "ollama":
            providers.append(OllamaProvider(s.secrets.ollama_url, model or cfg.ollama_model, cfg.ollama_think))
        elif name == "groq" and s.secrets.groq_api_key:
            providers.append(GroqProvider(s.secrets.groq_api_key, model))
        elif name == "gemini" and s.secrets.gemini_api_key:
            providers.append(GeminiProvider(s.secrets.gemini_api_key, model or cfg.gemini_model))
    return LLMChain(providers)
