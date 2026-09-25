import json

import pytest

from vidgen.config import get_settings
from vidgen.models import Scene
from vidgen.script import writer
from vidgen.script.llm import LLMChain, LLMError


class FakeProvider:
    """Returns queued responses in order; strings are returned raw, exceptions are raised."""

    def __init__(self, name, responses):
        self.name = name
        self.responses = list(responses)
        self.prompts = []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r)


def scene(narration, vtype="stock", q="ocean waves"):
    return {"narration": narration, "visual_query": q, "visual_type": vtype, "ai_prompt": ""}


SHORT = {
    "title": "Bermuda",
    "hook": "Ships vanish here.",
    "scenes": [scene("Ships vanish here."), scene("Fact one.", "ai_video"),
               scene("Fact two.", "ai_video"), scene("What do you think?")],
}


def test_split_narration_groups_sentences():
    text = " ".join(["One two three four five six seven eight nine ten."] * 3)
    parts = writer.split_narration(text, max_words=25)
    assert parts == [" ".join(["One two three four five six seven eight nine ten."] * 2),
                     "One two three four five six seven eight nine ten."]


def test_split_long_sentence_at_clauses():
    first = " ".join(["a"] * 15) + ","
    second = " ".join(["b"] * 15) + "."
    assert writer.split_narration(f"{first} {second}") == [first, second]


def test_split_long_sentence_without_punctuation():
    parts = writer.split_narration(" ".join(["w"] * 60))
    assert [len(p.split()) for p in parts] == [25, 25, 10]


def test_split_folds_tiny_fragment_into_neighbour():
    # real Gemini case: "Thực tế," + 24-word clause used to become a 2-word scene
    text = "Thực tế, " + " ".join(["từ"] * 24) + "."
    parts = writer.split_narration(text)
    assert len(parts) == 1 and parts[0].startswith("Thực tế,")


def test_split_tiny_last_fragment_joins_previous():
    text = " ".join(["a"] * 20) + ". Hết rồi."
    assert writer.split_narration(text, max_words=20) == [" ".join(["a"] * 20) + ". Hết rồi."]


def test_postprocess_drops_empty_narration():
    out = writer.postprocess([Scene(id=0, **scene("  ")), Scene(id=0, **scene("Hi."))], 1)
    assert [s.narration for s in out] == ["Hi."]


def test_postprocess_caps_ai_video_and_renumbers():
    scenes = [Scene(id=0, **scene(f"S{i}.", "ai_video")) for i in range(3)]
    out = writer.postprocess(scenes, max_ai_video=1)
    assert [s.id for s in out] == [1, 2, 3]
    assert [s.visual_type for s in out] == ["ai_video", "ai_image", "ai_image"]
    assert all(s.ai_prompt for s in out)


def test_postprocess_split_parts_after_first_use_stock():
    text = " ".join(["a b c d e f g h i j k l m n o."] * 2)  # 2×15 words > 25
    out = writer.postprocess([Scene(id=0, **scene(text, "ai_image"))], max_ai_video=1)
    assert [s.visual_type for s in out] == ["ai_image", "stock"]
    assert out[1].ai_prompt == ""


def test_generate_short():
    llm = LLMChain([FakeProvider("fake", [SHORT])])
    preset = get_settings().preset("short")
    script = writer.generate("Bermuda triangle", "short", "vi", preset, llm)
    assert script.format == "short" and script.lang == "vi"
    assert sum(s.visual_type == "ai_video" for s in script.scenes) <= preset.max_ai_video
    assert "Vietnamese" in llm.providers[0].prompts[0]


def test_generate_long_outline_then_chapters():
    outline = {"title": "Silk Road", "hook": "A road that changed the world.",
               "hook_visual_query": "desert caravan camels",
               "chapters": [{"title": f"C{i}", "summary": "s"} for i in range(3)]}
    chapter = {"scenes": [scene("Part one."), scene("Part two.")]}
    fake = FakeProvider("fake", [outline, chapter, chapter, chapter])
    preset = get_settings().preset("long").model_copy(update={"target_seconds": (240, 300)})
    script = writer.generate("Silk road", "long", "en", preset, LLMChain([fake]))
    assert script.scenes[0].chapter == "Intro"
    assert len(script.scenes) == 1 + 3 * 2
    assert len(fake.prompts) == 4
    assert "final chapter" in fake.prompts[-1]
    # max_ai_video=2 → chapters 1-2 get one slot, chapter 3 gets none
    assert "in this chapter: 1" in fake.prompts[1]
    assert "in this chapter: 0" in fake.prompts[3]


def test_chain_retries_invalid_json_then_falls_back():
    bad = FakeProvider("gemini", ["not json", "{}", "[]"])
    good = FakeProvider("ollama", [SHORT])
    result = LLMChain([bad, good], retries=2).generate("p", writer.ShortDraft)
    assert result.title == "Bermuda"
    assert len(bad.prompts) == 3  # first try + 2 retries


def test_chain_skips_provider_on_error():
    down = FakeProvider("gemini", [ConnectionError("quota")])
    good = FakeProvider("ollama", [SHORT])
    assert LLMChain([down, good]).generate("p", writer.ShortDraft).hook == "Ships vanish here."


def test_chain_all_fail():
    with pytest.raises(LLMError, match="All LLM providers failed"):
        LLMChain([FakeProvider("x", [RuntimeError("down")])]).generate("p", writer.ShortDraft)
