"""LLM providers returning JSON validated against a Pydantic model. Gemini first, Ollama fallback."""

import json
import logging
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from vidgen.config import Settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    pass


class LLMProvider(Protocol):
    name: str

    def generate_json(self, prompt: str, schema: type[BaseModel]) -> str: ...


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str, model: str):
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


class OllamaProvider:
    name = "ollama"

    def __init__(self, url: str, model: str):
        self._url = url.rstrip("/")
        self._model = model

    def generate_json(self, prompt: str, schema: type[BaseModel]) -> str:
        resp = httpx.post(
            f"{self._url}/api/chat",
            json={
                "model": self._model,
                "messages": [{"role": "user", "content": prompt}],
                "format": schema.model_json_schema(),
                "stream": False,
                "options": {"temperature": 0.8, "num_ctx": 16384},
            },
            timeout=600,
        )
        resp.raise_for_status()
        return resp.json()["message"]["content"]


class LLMChain:
    """Tries providers in order; each gets 1 + `retries` attempts to return schema-valid JSON."""

    def __init__(self, providers: list[LLMProvider], retries: int = 2):
        if not providers:
            raise LLMError("No LLM provider configured: set GEMINI_API_KEY or run Ollama")
        self.providers = providers
        self.retries = retries

    def generate(self, prompt: str, schema: type[T]) -> T:
        errors: list[str] = []
        for provider in self.providers:
            for attempt in range(1, self.retries + 2):
                try:
                    raw = provider.generate_json(prompt, schema)
                    return schema.model_validate(json.loads(raw))
                except (json.JSONDecodeError, ValidationError) as e:
                    errors.append(f"{provider.name}#{attempt}: invalid output: {e}")
                    log.warning("%s returned invalid JSON (attempt %d)", provider.name, attempt)
                except Exception as e:  # network, quota, auth → next provider
                    errors.append(f"{provider.name}: {type(e).__name__}: {e}")
                    log.warning("%s failed: %s", provider.name, e)
                    break
        raise LLMError("All LLM providers failed:\n" + "\n".join(errors))


def default_chain(s: Settings) -> LLMChain:
    providers: list[LLMProvider] = []
    if s.secrets.gemini_api_key:
        providers.append(GeminiProvider(s.secrets.gemini_api_key, s.pipeline.llm.gemini_model))
    providers.append(OllamaProvider(s.secrets.ollama_url, s.pipeline.llm.ollama_model))
    return LLMChain(providers)
