"""✨ topic enhance, and which model wrote the brief/script (shown in the studio, warned on fallback)."""

import json

import pytest

from vidgen.config import get_settings
from vidgen.script import enhance
from vidgen.script.llm import LLMChain, LLMError


class Answers:
    tier = "strong"

    def __init__(self, name, *answers, fail=False):
        self.name, self.answers, self.fail, self.prompts = name, list(answers), fail, []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        if self.fail:
            raise LLMError("down")
        return json.dumps(self.answers.pop(0))


def test_rough_ideas_become_specific_topics_without_invented_figures():
    llm = Answers("groq:x", {"ideas": [
        {"topic": "WannaCry 2017: vì sao một lỗi Windows đã vá vẫn làm tê liệt bệnh viện Anh?", "reason": "Cụ thể."},
        {"topic": "x", "reason": "quá ngắn"},
        {"topic": "Bạch tuộc: ba trái tim và dòng máu xanh", "reason": "Nghịch lý."}]})
    ideas = enhance.enhance_topics("wannacry\nbạch tuộc", "vi", "short", LLMChain([llm]))
    assert [i.topic for i in ideas] == ["WannaCry 2017: vì sao một lỗi Windows đã vá vẫn làm tê liệt bệnh viện Anh?",
                                        "Bạch tuộc: ba trái tim và dòng máu xanh"]
    assert "wannacry" in llm.prompts[0] and "Never add a number" in llm.prompts[0]


def test_nothing_usable_is_an_error():
    with pytest.raises(ValueError):
        enhance.enhance_topics("wannacry", "vi", "short", LLMChain([Answers("g", {"ideas": []})]))


def test_chain_usage_names_every_model_and_flags_a_fallback():
    chain = LLMChain([Answers("groq:a", fail=True), Answers("ollama:b", {"ideas": []}, {"ideas": []})])
    chain.generate("p", enhance.TopicIdeas)
    chain.generate("p", enhance.TopicIdeas)
    assert chain.usage() == {"models": ["ollama:b"], "fallback": True}
    assert LLMChain([Answers("groq:a")]).usage() == {"models": [], "fallback": False}


def test_brief_stage_records_which_model_wrote_it(tmp_path, monkeypatch):
    from vidgen import pipeline
    from vidgen.models import Angle, Brief

    (tmp_path / "state.json").write_text('{"topic": "t", "format": "short", "lang": "vi"}', encoding="utf-8")
    fake = LLMChain([Answers("groq:openai/gpt-oss-120b")])
    fake.used = ["groq:openai/gpt-oss-120b"]
    monkeypatch.setattr("vidgen.script.llm.default_chain", lambda s, role="creative": fake)
    monkeypatch.setattr("vidgen.script.brief.make_brief", lambda *a, **k: Brief(topic="t", angles=[
        Angle(style="explain", title="T", hook="h", key_points=["k"])]))
    pipeline.write_brief(tmp_path, get_settings())
    assert pipeline.load_state(tmp_path)["llm"]["brief"] == {"models": ["groq:openai/gpt-oss-120b"], "fallback": False}
