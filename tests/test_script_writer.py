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
        # the last queued response repeats, so extra length-retry calls get an answer too
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
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
    chapter_prompts = [p for p in fake.prompts if "Write chapter" in p]
    first_tries = [p for p in chapter_prompts if "far too short" not in p]
    assert len(first_tries) == 3
    assert "final chapter" in first_tries[-1]
    # max_ai_video=2 → chapters 1-2 get one slot, chapter 3 gets none
    assert "in this chapter: 1" in first_tries[0]
    assert "in this chapter: 0" in first_tries[2]


def test_short_draft_is_expanded_keeping_its_scenes():
    extra = [scene(" ".join(["mới"] * 20) + ".") for _ in range(8)]
    expanded = {"scenes": SHORT["scenes"][:2] + extra + SHORT["scenes"][2:]}  # originals kept, in order
    fake = FakeProvider("fake", [SHORT, expanded])
    script = writer.generate("x", "short", "vi", get_settings().preset("short"), LLMChain([fake]))
    assert len(fake.prompts) == 2 and "You are extending a video script" in fake.prompts[1]
    assert "Ships vanish here." in fake.prompts[1]          # the draft is shown to the model
    assert script.word_count > 150 and script.scenes[0].narration == "Ships vanish here."


def test_expansion_that_drops_original_scenes_is_rejected():
    rewritten = {"scenes": [scene(" ".join(["khác"] * 20) + ".") for _ in range(12)]}
    fake = FakeProvider("fake", [SHORT, rewritten])
    script = writer.generate("x", "short", "vi", get_settings().preset("short"), LLMChain([fake]))
    assert script.title == "Bermuda" and script.word_count < 20  # kept the draft


def test_short_draft_keeps_first_when_retry_is_not_longer():
    fake = FakeProvider("fake", [SHORT])  # retry returns the same short draft
    script = writer.generate("x", "short", "vi", get_settings().preset("short"), LLMChain([fake]))
    assert len(fake.prompts) == 2 and script.title == "Bermuda"


def test_long_enough_draft_is_not_retried():
    long_draft = {"title": "t", "hook": "h", "scenes": [scene(" ".join(["từ"] * 20) + ".")] * 12}
    fake = FakeProvider("fake", [long_draft])
    writer.generate("x", "short", "vi", get_settings().preset("short"), LLMChain([fake]))
    assert len(fake.prompts) == 1


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


def test_extend_script_by_chapter_skips_intro():
    from vidgen.models import Script
    s = Script(title="t", hook="h", lang="en", format="long", scenes=[
        Scene(id=1, narration="Hook sentence here.", visual_query="q", chapter="Intro"),
        Scene(id=2, narration="Fact one is short.", visual_query="q", chapter="A"),
        Scene(id=3, narration="Fact two is short.", visual_query="q", chapter="B")])

    class Grow:
        name = "grow"
        prompts = []

        def generate_json(self, prompt, schema):
            self.prompts.append(prompt)
            data = json.loads(prompt.split("Current scenes (JSON, in order):")[1].split("The script has")[0])
            return json.dumps({"scenes": data + [scene(" ".join(["more"] * 20) + ".")]})

    preset = get_settings().preset("long")
    out = writer.extend_script(s, preset, LLMChain([Grow()]))
    assert out.scenes[0].narration == "Hook sentence here." and out.scenes[0].chapter == "Intro"
    assert [sc.chapter for sc in out.scenes].count("A") == 2 and len(Grow.prompts) == 2
    assert [sc.id for sc in out.scenes] == list(range(1, len(out.scenes) + 1))


def test_extend_script_noop_when_long_enough():
    from vidgen.models import Script
    long_text = " ".join(["từ"] * 250) + "."
    s = Script(title="t", hook="h", lang="vi", format="short",
               scenes=[Scene(id=1, narration=long_text, visual_query="q")])
    assert writer.extend_script(s, get_settings().preset("short"), LLMChain([FakeProvider("x", [SHORT])])) is None


def test_expansion_that_moves_the_closing_question_is_rejected():
    extra = [scene(" ".join(["mới"] * 20) + ".") for _ in range(8)]
    moved = {"scenes": SHORT["scenes"][:3] + extra[:4] + [SHORT["scenes"][3]] + extra[4:]}  # question not last
    fake = FakeProvider("fake", [SHORT, moved])
    script = writer.generate("x", "short", "vi", get_settings().preset("short"), LLMChain([fake]))
    assert script.scenes[-1].narration == "What do you think?" and script.word_count < 20


def test_keeps_originals_requires_order():
    a, b, c = (writer.LLMScene(narration=t, visual_query="q") for t in ("A.", "B.", "C."))
    n = writer.LLMScene(narration="N.", visual_query="q")
    assert writer._keeps_originals([a, b, c], [a, n, b, n, c])
    assert not writer._keeps_originals([a, b, c], [a, c, b, n, c])   # reordered / duplicated
    assert writer._keeps_originals([a, b], [a, b, n], pin_last=False)  # middle chapter may grow at its end
    assert not writer._keeps_originals([a, b, c], [a, b, n])         # closing dropped


def test_extend_script_keeps_non_contiguous_chapters_in_place():
    from vidgen.models import Script
    s = Script(title="t", hook="h", lang="en", format="long", scenes=[
        Scene(id=1, narration="A one.", visual_query="q", chapter="A"),
        Scene(id=2, narration="B one.", visual_query="q", chapter="B"),
        Scene(id=3, narration="A two.", visual_query="q", chapter="A")])  # moved across the border

    class Same:
        name = "same"

        def generate_json(self, prompt, schema):
            data = json.loads(prompt.split("Current scenes (JSON, in order):")[1].split("The script has")[0])
            first, rest = data[0], data[1:]
            return json.dumps({"scenes": [first, scene(" ".join(["x"] * 20) + ".")] + rest})

    out = writer.extend_script(s, get_settings().preset("long"), LLMChain([Same()]))
    order = [sc.chapter for sc in out.scenes]
    assert order == ["A", "A", "B", "B", "A"]  # runs extended in place, nothing merged; closing kept last


def test_postprocess_cleans_alt_queries():
    sc = Scene(id=0, narration="Hi.", visual_query="phone screen link",
               alt_queries=["finger tapping phone", "Phone screen link", "bàn tay", "laptop inbox", "extra one"])
    [out] = writer.postprocess([sc], 1)
    assert out.alt_queries == ["finger tapping phone", "laptop inbox"]  # English, not the main query, max 2


def test_short_prompt_asks_for_alt_queries():
    from vidgen.script.writer import LLMScene

    assert "alt_queries" in LLMScene.model_fields
    assert "alt_queries" in (writer.PROMPTS / "short.md").read_text(encoding="utf-8")
