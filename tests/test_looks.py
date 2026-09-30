"""Cinematic visuals, phase 4: one colour look per video (the writer picks it, a series can pin it)."""

import json
from pathlib import Path

import pytest

from vidgen.assemble import looks
from vidgen.assemble.clips import Shot, _stream, plan_shots
from vidgen.config import get_settings
from vidgen.models import Asset, SceneAudio, Script, Scene, Timeline
from vidgen.script import writer
from vidgen.script.llm import LLMChain
from vidgen.script.templates import PROMPTS, render

P = get_settings().preset("short")


def test_neutral_is_exactly_the_old_grade():
    assert looks.grade("neutral", 0.05) == \
        "eq=brightness=0.050:contrast=1.04:saturation=1.08,vignette=angle=0.45,noise=alls=3:allf=t"


@pytest.mark.parametrize("look, marker", [("dark_mystery", "bs=0.09"), ("cyber_tech", "rh=0.11"),
                                          ("vintage_archive", "colorchannelmixer")])
def test_each_look_has_its_own_tone(look, marker):
    assert marker in looks.grade(look, 0.0)


def test_unknown_look_falls_back_to_neutral():
    assert looks.normalize_look("Teal-Orange!") == "neutral" and looks.normalize_look(" CYBER_TECH ") == "cyber_tech"


def test_shots_carry_the_look_but_evidence_cards_stay_neutral():
    tl = Timeline(scenes=[SceneAudio(scene_id=i, path="", start=3 * (i - 1), duration=3, words=[]) for i in (1, 2)])
    assets = [Asset(scene_id=1, path="visuals/a.mp4", kind="video", source="pexels"),
              Asset(scene_id=2, path="visuals/scene_002_doc.mp4", kind="video", source="document")]
    shots = plan_shots(tl, assets, P, "short", Path("o"), {}, look="cyber_tech")
    assert [s.look for s in shots] == ["cyber_tech", "neutral"]
    assert "rh=0.11" in _stream(shots[0], P, 0, 90, 90) and "rh=0.11" not in _stream(shots[1], P, 0, 90, 90)


def test_the_writer_asks_for_a_look_and_keeps_a_valid_one():
    for name in ("short.md", "long_outline.md"):
        for tier in ("base", "strong"):
            text = render(name, tier=tier, topic="t", facts="", angle="", lang_name="English", target_words=1,
                          target_seconds=1, scene_range="1", max_ai_video=0, target_minutes=1, chapter_count=3)
            assert "`look`" in text and "cyber_tech" in text, (name, tier)

    class Draft:
        name, tier = "fake", "base"

        def generate_json(self, prompt, schema):
            return json.dumps({"title": "T", "hook": "Một mã độc khoá bệnh viện.", "mood": "tense", "look": "cyber_tech",
                               "open_loop": "Vì sao?", "payoff_scene": 3,
                               "scenes": [{"narration": n, "visual_query": "server room"} for n in
                                          ("Một mã độc khoá bệnh viện.", "Vì sao?", "Vì chưa vá.", "Bạn nghĩ sao?")]})
    script = writer.generate("WannaCry", "short", "vi", P, LLMChain([Draft()]))
    assert script.look == "cyber_tech"
    assert Script(title="t", hook="h", lang="vi", format="short",
                  scenes=[Scene(id=1, narration="n", visual_query="q")]).look == "neutral"


def test_a_series_can_pin_the_channel_look():
    from vidgen import series as sr
    s = sr.load(Path(__file__).resolve().parents[1] / "series" / "cyber-security.yaml")
    assert s.look == "cyber_tech"


def test_a_misspelt_series_look_is_an_error():
    from vidgen import series as sr
    with pytest.raises(ValueError):
        sr.Series.model_validate({"name": "t", "start": "2026-10-05", "weekdays": ["mon"], "langs": ["vi"],
                                  "look": "cyber", "episodes": []})
