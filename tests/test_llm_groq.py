"""Groq (OpenAI-compatible, free tier) for the creative text tasks; local Ollama stays the checker and the
fallback. No network: httpx.post is replaced."""

import json

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from vidgen.config import LLMConfig, get_settings
from vidgen.script import llm as L


class Out(BaseModel):
    hook: str
    n: int = 1


class Sub(BaseModel):
    text: str


class Nested(BaseModel):
    items: list[Sub]
    note: str = ""


def reply(status=200, content=None, headers=None):
    body = {"choices": [{"message": {"content": json.dumps(content)}}]} if status == 200 else {"error": {"message": "x"}}
    return httpx.Response(status, json=body, headers=headers or {})


def fake_post(monkeypatch, *responses):
    calls = []
    queue = list(responses)

    def post(url, **kw):
        calls.append((url, kw))
        return queue.pop(0)
    monkeypatch.setattr(L.httpx, "post", post)
    return calls


def groq(sleeps=None):
    return L.GroqProvider("gsk_test", "openai/gpt-oss-120b", sleep=(sleeps.append if sleeps is not None else None))


# --- provider ---------------------------------------------------------------------------------------------
def test_groq_asks_for_strict_json_schema_and_parses(monkeypatch):
    calls = fake_post(monkeypatch, reply(content={"hook": "WannaCry", "n": 2}))
    out = L.LLMChain([groq()]).generate("p", Out)
    assert out == Out(hook="WannaCry", n=2)
    url, kw = calls[0]
    assert url == "https://api.groq.com/openai/v1/chat/completions"
    assert kw["headers"]["Authorization"] == "Bearer gsk_test"
    fmt = kw["json"]["response_format"]
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True
    assert kw["json"]["reasoning_effort"] == "low"          # gpt-oss: reasoning tokens eat the 8k/min budget


def test_strict_schema_closes_every_object_and_requires_every_field():
    s = L.strict_schema(Nested)
    objects = [s] + list(s.get("$defs", {}).values())
    for o in objects:
        assert o["additionalProperties"] is False and set(o["required"]) == set(o["properties"])
        assert all("default" not in p for p in o["properties"].values())


def test_rate_limit_waits_the_advised_time_then_succeeds(monkeypatch):
    sleeps = []
    fake_post(monkeypatch, reply(429, headers={"retry-after": "3"}), reply(content={"hook": "h"}))
    assert L.LLMChain([groq(sleeps)]).generate("p", Out).hook == "h"
    assert sleeps == [3.0]


class Local:
    name, tier = "ollama:qwen3:8b", "base"

    def __init__(self):
        self.prompts = []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        return json.dumps({"hook": "local"})


def test_daily_cap_or_bad_key_moves_on_without_waiting(monkeypatch):
    for resp in (reply(429, headers={"retry-after": "3600"}), reply(401)):
        sleeps = []
        fake_post(monkeypatch, resp)
        chain = L.LLMChain([groq(sleeps), Local()])
        assert chain.generate("p", Out).hook == "local"
        assert sleeps == [] and chain.last_provider == "ollama:qwen3:8b" and chain.fallback is True


def test_schema_rejected_falls_back_to_json_object_mode(monkeypatch):
    calls = fake_post(monkeypatch, reply(400), reply(content={"hook": "h"}))
    assert L.LLMChain([groq()]).generate("p", Out).hook == "h"
    assert calls[1][1]["json"]["response_format"] == {"type": "json_object"}
    assert "hook" in calls[1][1]["json"]["messages"][0]["content"]      # the schema travels in the prompt


def test_first_provider_answering_is_not_a_fallback(monkeypatch):
    fake_post(monkeypatch, reply(content={"hook": "h"}))
    chain = L.LLMChain([groq(), Local()])
    chain.generate("p", Out)
    assert chain.last_provider == "groq:openai/gpt-oss-120b" and chain.fallback is False


# --- roles / config ------------------------------------------------------------------------------------------
def settings(creative, checker=("ollama",), key=""):
    s = get_settings().model_copy(deep=True)
    s.pipeline.llm = LLMConfig(creative=list(creative), checker=list(checker))
    s.secrets.groq_api_key = key
    return s


def test_roles_pick_their_chains_and_skip_groq_without_a_key():
    specs = ["groq:openai/gpt-oss-120b", "groq:qwen/qwen3.8-27b", "ollama"]
    with_key = settings(specs, key="gsk_x")
    assert [p.name for p in L.default_chain(with_key).providers] == \
        ["groq:openai/gpt-oss-120b", "groq:qwen/qwen3.8-27b", "ollama:qwen3:8b"]
    assert [p.name for p in L.default_chain(with_key, "checker").providers] == ["ollama:qwen3:8b"]
    assert [p.name for p in L.default_chain(settings(specs)).providers] == ["ollama:qwen3:8b"]   # $0 path as before
    assert [p.tier for p in L.default_chain(with_key).providers] == ["strong", "strong", "base"]


def test_config_rejects_unknown_providers_and_the_old_key():
    with pytest.raises(ValidationError):
        LLMConfig(creative=["grok:x"])
    with pytest.raises(ValidationError):
        LLMConfig(creative=["groq"])                       # groq needs a model
    with pytest.raises(ValidationError):
        LLMConfig(providers=["ollama"])
