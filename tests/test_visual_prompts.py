"""Cinematic visuals, phase 1: scripts ask for visuals like a director of photography — a metaphor when the idea
can't be filmed, and a shot note (camera, subject, light, setting) for every scene so an AI fallback draws it."""

from pathlib import Path

import pytest

from vidgen.models import Scene
from vidgen.script import writer
from vidgen.script.templates import PROMPTS

SCENE_PROMPTS = ("short.md", "long_chapter.md", "expand.md", "rewrite_scene.md")


@pytest.mark.parametrize("name", SCENE_PROMPTS)
@pytest.mark.parametrize("tier", ("", "strong/"))
def test_every_scene_prompt_asks_for_metaphors_and_a_shot_note(name, tier):
    text = (PROMPTS / f"{tier}{name}").read_text(encoding="utf-8")
    assert "metaphor" in text
    assert "`ai_prompt`: for EVERY scene" in text and "lighting" in text and "camera" in text
    assert "Empty string for stock" not in text and "empty string for stock" not in text


def test_strong_prompts_add_shot_variety():
    assert "director of photography" in (PROMPTS / "strong" / "short.md").read_text(encoding="utf-8")


def test_stock_scenes_and_every_split_part_keep_their_shot_note():
    note = "Extreme macro of an eye reflecting green code, neon rim light, dark room, vertical"
    long_text = "Một câu khá dài để bị tách làm hai phần. " * 4
    out = writer.postprocess([Scene(id=0, narration=long_text.strip(), visual_query="eye reflecting code",
                                    visual_type="stock", ai_prompt=note)], 0)
    assert len(out) > 1 and all(s.ai_prompt == note for s in out)


def test_a_rewritten_scene_brings_its_new_shot_note(tmp_path, monkeypatch):
    from vidgen import pipeline
    from vidgen.config import get_settings
    from vidgen.models import Script
    script = Script(title="t", hook="h", lang="vi", format="short",
                    scenes=[Scene(id=1, narration="Mở đầu.", visual_query="q", ai_prompt="old")])
    (tmp_path / "script.json").write_text(script.model_dump_json(), encoding="utf-8")
    new = writer.LLMScene(narration="Mới.", visual_query="burning banknotes", ai_prompt="Low angle, burning cash")
    monkeypatch.setattr("vidgen.script.writer.rewrite_one", lambda *a, **k: new)
    monkeypatch.setattr("vidgen.script.llm.default_chain", lambda s, role="creative": None)
    out = pipeline.rewrite_scene(tmp_path, get_settings(), 1)
    assert out["ai_prompt"] == "Low angle, burning cash"


def test_the_word_metaphor_never_reaches_stock_search_and_notes_are_plain():
    out = writer.postprocess([Scene(id=0, narration="Một câu.", visual_query="broken shield metaphor",
                                    alt_queries=["visual metaphor of virus spreading", "abstract symbolic cracked padlock"],
                                    ai_prompt="close\u2011up of a cracked shield, rim light")], 0)
    s = out[0]
    assert s.visual_query == "broken shield"
    assert s.alt_queries == ["virus spreading", "abstract cracked padlock"]   # "abstract" is a real word
    assert s.ai_prompt == "close-up of a cracked shield, rim light"


def test_inserted_hook_and_question_scenes_keep_a_shot_note():
    from vidgen.models import Angle
    note = "Low angle of an empty hospital corridor, flickering light"
    scenes = [Scene(id=0, narration=n, visual_query="hospital corridor", ai_prompt=note) for n in
              ("Một cảnh mở đầu khác hẳn.", "Cảnh hai nói về bệnh viện.", "Cảnh ba.")]
    angle = Angle(style="story", title="T", hook="Bệnh viện Anh tê liệt trong một ngày.", key_points=["k"])
    out = writer._open_loop_structure(scenes, "Vì sao lại như vậy?", angle, 0)
    assert all(s.ai_prompt for s in out)


@pytest.mark.parametrize("query, kept", [("abstract glitch effect macro", "abstract glitch effect macro"),
                                         ("concept car in the rain", "concept car in the rain"),
                                         ("broken shield metaphor", "broken shield"),
                                         ("symbolic cracked padlock", "cracked padlock")])
def test_only_metaphor_words_are_stripped(query, kept):
    """Review finding 6: 'abstract'/'concept' are real words ('concept car')."""
    assert writer.strip_meta(query) == kept


def test_a_query_that_was_only_meta_words_borrows_an_alternative():
    out = writer.postprocess([Scene(id=0, narration="Một câu.", visual_query="visual metaphor",
                                    alt_queries=["cracked padlock macro"])], 0)
    assert out[0].visual_query == "cracked padlock macro"


def test_a_rewritten_scene_gets_the_same_cleaning(monkeypatch):
    """Review finding 7: the rewrite path skipped strip_meta and plain()."""
    import json as _json

    from vidgen.models import Script
    from vidgen.script.llm import LLMChain

    class One:
        name = "fake"

        def generate_json(self, prompt, schema):
            return _json.dumps({"narration": "Mới.", "visual_query": "broken shield metaphor",
                                "alt_queries": ["visual metaphor of trust"], "ai_prompt": "close\u2011up, rim light"})
    script = Script(title="t", hook="h", lang="vi", format="short",
                    scenes=[Scene(id=1, narration="Cũ.", visual_query="q")])
    out = writer.rewrite_one(script, script.scenes[0], "", "", "x", [], None, LLMChain([One()]))
    assert out.visual_query == "broken shield" and out.alt_queries == ["trust"] and out.ai_prompt == "close-up, rim light"
