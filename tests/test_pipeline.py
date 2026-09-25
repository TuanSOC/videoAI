from datetime import date

import pytest

from vidgen import pipeline
from vidgen.config import get_settings
from vidgen.models import Scene, Script


@pytest.fixture
def fake_stages(monkeypatch):
    """Replace stage bodies with ones that just write their artifact and record calls."""
    calls: list[str] = []

    def fake(st):
        def run(job):
            calls.append(st.name)
            (job.out_dir / st.artifact).write_text("x", encoding="utf-8")
        return run

    stages = [pipeline.Stage(st.name, st.artifact, st.extra, fake(st)) for st in pipeline.STAGES]
    monkeypatch.setattr(pipeline, "STAGES", stages)
    return calls


def write_script(out_dir, narration="Hello."):
    out_dir.mkdir(parents=True, exist_ok=True)
    s = Script(title="t", hook="h", lang="en", format="short",
               scenes=[Scene(id=1, narration=narration, visual_query="q")])
    (out_dir / "script.json").write_text(s.model_dump_json(), encoding="utf-8")


def test_runs_all_then_skips(tmp_path, fake_stages):
    write_script(tmp_path)
    pipeline.run_stages(tmp_path, get_settings())
    assert fake_stages == ["voice", "visuals", "render", "metadata"]
    fake_stages.clear()
    pipeline.run_stages(tmp_path, get_settings())
    assert fake_stages == []


def test_resume_after_crash_skips_done_stages(tmp_path, fake_stages, monkeypatch):
    write_script(tmp_path)

    def boom(job):
        raise RuntimeError("visuals crashed")

    stages = list(pipeline.STAGES)
    stages[1] = pipeline.Stage("visuals", "assets.json", ("visuals",), boom)
    monkeypatch.setattr(pipeline, "STAGES", stages)
    with pytest.raises(RuntimeError):
        pipeline.run_stages(tmp_path, get_settings())
    assert fake_stages == ["voice"]


def test_editing_script_invalidates_downstream(tmp_path, fake_stages):
    write_script(tmp_path)
    pipeline.run_stages(tmp_path, get_settings())
    fake_stages.clear()
    write_script(tmp_path, narration="Edited.")
    pipeline.run_stages(tmp_path, get_settings())
    assert fake_stages == ["voice", "visuals", "render", "metadata"]


def test_force_redoes_stage_and_after(tmp_path, fake_stages):
    write_script(tmp_path)
    pipeline.run_stages(tmp_path, get_settings())
    fake_stages.clear()
    pipeline.run_stages(tmp_path, get_settings(), force="render")
    assert fake_stages == ["render", "metadata"]


def test_invalidate_removes_owned_dirs(tmp_path):
    (tmp_path / "segments").mkdir()
    (tmp_path / "final.mp4").write_text("x")
    (tmp_path / "script.json").write_text("{}")
    assert pipeline.invalidate_from(tmp_path, "render") == ["render"]
    assert not (tmp_path / "segments").exists()
    assert (tmp_path / "script.json").exists()


def test_new_output_dir_suffixes_collisions(tmp_path, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s.pipeline, "output_dir", tmp_path)
    a = pipeline.new_output_dir("Bí ẩn tam giác Bermuda", s, date(2026, 9, 25))
    b = pipeline.new_output_dir("Bí ẩn tam giác Bermuda", s, date(2026, 9, 25))
    assert a.name == "bi-an-tam-giac-bermuda-260925"
    assert b.name == "bi-an-tam-giac-bermuda-260925-2"
