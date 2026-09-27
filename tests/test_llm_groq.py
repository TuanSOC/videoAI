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


# --- prompt tiers ------------------------------------------------------------------------------------------
class Strong:
    tier = "strong"

    def __init__(self, fail=False, answer=None):
        self.name, self.fail, self.answer, self.prompts = "groq:x", fail, answer, []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        if self.fail:
            raise L.LLMError("down")
        return json.dumps(self.answer)


def test_each_provider_gets_the_prompt_for_its_tier():
    strong, local = Strong(fail=True), Local()
    L.LLMChain([strong, local]).generate(lambda tier: f"<{tier}>", Out)
    assert strong.prompts == ["<strong>"] and local.prompts == ["<base>"]   # the 8B never sees the rich prompt
    assert L.LLMChain([Local()]).generate("plain", Out).hook == "local"     # plain strings still work


def test_strong_prompt_file_wins_only_for_strong_and_only_when_it_exists():
    from vidgen.script.templates import PROMPTS, render
    base = render("factcheck.md", facts="F", lang_name="Vietnamese", scenes="1. x")
    assert render("factcheck.md", tier="strong", facts="F", lang_name="Vietnamese", scenes="1. x") == base
    assert (PROMPTS / "strong" / "short.md").exists()


def test_strong_prompts_need_no_value_the_base_call_lacks():
    import re

    from vidgen.script.templates import PROMPTS

    def placeholders(p):
        return set(re.findall(r"\$(\w+)", p.read_text(encoding="utf-8")))
    strong = list((PROMPTS / "strong").glob("*.md"))
    assert {p.name for p in strong} >= {"short.md", "brief_angles.md", "long_outline.md", "long_chapter.md",
                                        "expand.md", "rewrite_scene.md", "hook_fix.md"}
    for p in strong:
        assert placeholders(p) <= placeholders(PROMPTS / p.name) | {"moods"}, p.name


def test_writer_sends_the_storytelling_prompt_to_a_strong_model():
    from vidgen.script import writer
    draft = {"title": "T", "hook": "Một lỗi Windows làm tê liệt bệnh viện.", "mood": "tense",
             "open_loop": "Vì sao bản vá có sẵn mà vẫn thua?", "payoff_scene": 3,
             "scenes": [{"narration": n, "visual_query": "server room", "alt_queries": [], "visual_type": "stock",
                         "ai_prompt": ""} for n in ("Một lỗi Windows làm tê liệt bệnh viện.",
                                                    "Vì sao bản vá có sẵn mà vẫn thua?", "Vì máy chưa cập nhật.",
                                                    "Bạn đã cập nhật chưa?")]}
    strong = Strong(answer=draft)
    writer.generate("WannaCry", "short", "vi", get_settings().preset("short"), L.LLMChain([strong]))
    assert "Storytelling" in strong.prompts[0]


def test_strong_prompts_keep_every_rule_of_the_base_ones():
    from vidgen.script.templates import PROMPTS
    for p in (PROMPTS / "strong").glob("*.md"):
        strong = p.read_text(encoding="utf-8")
        rules = [ln for ln in (PROMPTS / p.name).read_text(encoding="utf-8").splitlines()
                 if ln.lstrip().startswith(("- ", "1.", "2.", "4.", "5.")) and "2-4 concrete" not in ln]
        assert [r for r in rules if r not in strong] == [], p.name


def test_expansion_in_the_wrong_language_is_dropped():
    from vidgen.script import writer

    class Adds:
        name, tier = "groq:x", "strong"

        def generate_json(self, prompt, schema):
            return json.dumps({"new_scenes": [
                {"narration": "The exploit was leaked in April 2017.", "visual_query": "code", "alt_queries": [],
                 "visual_type": "stock", "ai_prompt": "", "after": 1},
                {"narration": "Mã khai thác bị rò rỉ vào tháng 4/2017.", "visual_query": "code", "alt_queries": [],
                 "visual_type": "stock", "ai_prompt": "", "after": 1}]})
    scenes = [writer.LLMScene(narration=n, visual_query="q") for n in ("Mở đầu.", "Kết thúc?")]
    out = writer.expand_scenes(scenes, 40, {"facts": "", "angle": ""}, "vi", L.LLMChain([Adds()]))
    assert [s.narration for s in out] == ["Mở đầu.", "Mã khai thác bị rò rỉ vào tháng 4/2017.", "Kết thúc?"]


def test_typographic_hyphens_and_spaces_are_plain_in_narration():
    from vidgen.models import Scene
    from vidgen.script import writer
    [s] = writer.postprocess([Scene(id=0, narration="Ngày 14\u20114\u20112017, 200\u00a0000 máy.", visual_query="q")], 0)
    assert s.narration == "Ngày 14-4-2017, 200 000 máy."


def test_strong_prompts_forbid_unpronounceable_strings():
    from vidgen.script.templates import PROMPTS
    spoken = ("short.md", "long_chapter.md", "expand.md", "rewrite_scene.md", "hook_fix.md")
    assert all("domain names" in (PROMPTS / "strong" / n).read_text(encoding="utf-8") for n in spoken)
