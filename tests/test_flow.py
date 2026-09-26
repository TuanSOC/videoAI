"""Workflow fixes from the full review: re-render semantics, stale detection, stage consistency, voice
settings, retry of failed briefs, busy folders, config strictness, model sharing."""

import json

import pytest

from vidgen import pipeline
from vidgen.config import get_settings
from vidgen.models import Scene, Script


@pytest.fixture
def fake_stages(monkeypatch):
    calls: list[str] = []

    def fake(st):
        def run(job):
            calls.append(st.name)
            (job.out_dir / st.artifact).write_text("x", encoding="utf-8")
        return run

    monkeypatch.setattr(pipeline, "STAGES", [pipeline.Stage(st.name, st.artifact, st.extra, fake(st))
                                             for st in pipeline.STAGES])
    return calls


def write_script(out_dir, narration="Hello."):
    s = Script(title="t", hook="h", lang="en", format="short", scenes=[Scene(id=1, narration=narration, visual_query="q")])
    (out_dir / "script.json").write_text(s.model_dump_json(), encoding="utf-8")


def test_rerender_keeps_metadata(tmp_path, fake_stages):
    write_script(tmp_path)
    pipeline.run_stages(tmp_path, get_settings())
    fake_stages.clear()
    pipeline.run_stages(tmp_path, get_settings(), force="render")
    assert fake_stages == ["render"]                 # metadata is refreshed by the render, no LLM call


def test_a_regenerated_stage_redoes_what_was_built_on_it(tmp_path, fake_stages):
    write_script(tmp_path)
    pipeline.run_stages(tmp_path, get_settings())
    (tmp_path / "assets.json").unlink()              # clips lost, final.mp4 still there
    fake_stages.clear()
    pipeline.run_stages(tmp_path, get_settings())
    assert fake_stages == ["visuals", "render"]      # the video is rebuilt from the new clips


def test_changing_the_voice_settings_revoices(tmp_path, fake_stages):
    write_script(tmp_path)
    s = get_settings().model_copy(deep=True)
    pipeline.run_stages(tmp_path, s)
    fake_stages.clear()
    s.pipeline.voice_rate = "+20%"
    pipeline.run_stages(tmp_path, s)
    assert fake_stages[:1] == ["voice"]
    fake_stages.clear()
    pipeline.run_stages(tmp_path, s)
    assert fake_stages == []


def test_script_changed_after_render(tmp_path, fake_stages):
    write_script(tmp_path)
    pipeline.run_stages(tmp_path, get_settings())
    assert not pipeline.script_changed(tmp_path)
    write_script(tmp_path, "Edited.")
    assert pipeline.script_changed(tmp_path)


def test_busy_folder_is_refused(tmp_path):
    (tmp_path / "job.json").write_text(json.dumps({"slug": "x", "kind": "render", "status": "running"}))
    with pytest.raises(pipeline.FolderBusyError):
        pipeline.ensure_not_busy(tmp_path)
    (tmp_path / "job.json").write_text(json.dumps({"slug": "x", "kind": "render", "status": "done"}))
    pipeline.ensure_not_busy(tmp_path)


# --- config -----------------------------------------------------------------------------------------------
def test_config_typos_are_errors():
    from pydantic import ValidationError

    from vidgen.config import PipelineConfig

    with pytest.raises(ValidationError):
        PipelineConfig(formats={}, voices={}, sfx={"enable": False})


def test_env_values_cannot_inject_lines(tmp_path):
    from vidgen.config import update_env_file

    env = tmp_path / ".env"
    with pytest.raises(ValueError):
        update_env_file({"PEXELS_API_KEY": "abc\nGEMINI_API_KEY=stolen"}, env)
    update_env_file({"PEXELS_API_KEY": "  abc  "}, env)
    assert env.read_text(encoding="utf-8") == "PEXELS_API_KEY=abc\n"


# --- vision model shared between jobs --------------------------------------------------------------------
def test_vision_model_unloaded_only_when_the_last_user_is_done(tmp_path, monkeypatch):
    from vidgen.visuals import selector as sel
    from vidgen.visuals import vision

    calls = []
    monkeypatch.setattr(vision, "unload", lambda url, model, http=None: calls.append(model))
    s = get_settings()

    def selector_():
        x = sel.Selector([], None, s.preset("short"), tmp_path, tmp_path, vision=vision.VisionJudge("http://x", "vl"))
        x.vision.used = True
        return x
    a, b = selector_(), selector_()
    with sel.vision_session(a, s):
        with sel.vision_session(b, s):
            pass
        assert calls == []                           # the other job is still judging
    assert calls == [s.pipeline.vision.model]


# --- shared helpers ---------------------------------------------------------------------------------------
def test_lang_names_live_in_config():
    from vidgen.config import LANG_NAMES
    from vidgen.script import writer

    assert LANG_NAMES == {"vi": "Vietnamese", "en": "English"} and writer.LANG_NAMES is LANG_NAMES


def test_prompts_use_the_mood_list_from_code():
    from vidgen.assemble.music import MOODS
    from vidgen.script.templates import render

    prompt = render("short.md", topic="t", facts="", angle="", lang_name="English", target_words=1,
                    target_seconds=1, scene_range="1", max_ai_video=0)
    assert all(m in prompt for m in MOODS) and "$moods" not in prompt
