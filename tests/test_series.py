"""A series: a YAML frame of episodes on a weekly rhythm; `vidgen series` makes whatever is due, once."""

import json
from datetime import date
from pathlib import Path

import pytest

from vidgen import series as sr
from vidgen.models import Angle, Brief

ROOT = Path(__file__).resolve().parents[1]


def frame(**kw):
    data = {"name": "t", "start": "2026-10-05", "weekdays": ["mon", "wed", "fri"], "format": "short",
            "langs": ["vi", "en"],
            "episodes": [{"id": i, "pillar": "p", "angle": "story", "topic": {"vi": f"Chủ đề {i}", "en": f"Topic {i}"}}
                         for i in (1, 2, 3, 4)]}
    data.update(kw)
    return sr.Series.model_validate(data)


def test_episodes_fall_on_the_weekly_slots_in_order():
    dates = [d for d, _ in sr.schedule(frame())]
    assert dates == [date(2026, 10, 5), date(2026, 10, 7), date(2026, 10, 9), date(2026, 10, 12)]


def test_an_episode_can_pin_its_own_date_and_the_rest_keep_their_slots():
    s = frame()
    s.episodes[1].date = date(2026, 10, 31)
    assert [d for d, _ in sr.schedule(s)] == [date(2026, 10, 5), date(2026, 10, 31), date(2026, 10, 7),
                                            date(2026, 10, 9)]


def test_due_items_are_each_language_of_each_episode_not_made_yet():
    items = sr.due(frame(), today=date(2026, 10, 7), state={"1/vi": "slug-1-vi"})
    assert [(e.id, lang) for _, e, lang in items] == [(1, "en"), (2, "vi"), (2, "en")]


def test_a_frame_with_a_missing_translation_or_duplicate_ids_is_rejected():
    with pytest.raises(ValueError):
        frame(episodes=[{"id": 1, "pillar": "p", "angle": "story", "topic": {"vi": "x"}}])
    with pytest.raises(ValueError):
        frame(episodes=[{"id": 1, "pillar": "p", "angle": "story", "topic": {"vi": "x", "en": "y"}}] * 2)


def test_the_angle_is_picked_by_style_not_position():
    brief = Brief(topic="t", angles=[Angle(style=s, title=s, hook="h", key_points=["k"]) for s in ("explain", "story")])
    assert sr.angle_index(brief, "story") == 1 and sr.angle_index(brief, "myth") == 0   # missing style: first


def test_the_shipped_cyber_security_frame_is_valid():
    s = sr.load(ROOT / "series" / "cyber-security.yaml")
    assert len(s.episodes) == 12 and s.langs == ["vi", "en"] and s.weekdays == ["mon", "wed", "fri"]
    assert {e.angle for e in s.episodes} == {"explain", "myth", "story"}
    assert len({e.pillar for e in s.episodes}) == 3


def test_run_makes_due_videos_once_and_resumes_an_unfinished_one(tmp_path, monkeypatch):
    path = tmp_path / "s.yaml"
    path.write_text(json.dumps(frame().model_dump(mode="json")), encoding="utf-8")   # JSON is valid YAML
    made, rendered = [], []

    def create(ep, lang, s, fmt, settings):
        made.append((ep.id, lang))
        d = tmp_path / f"v{ep.id}{lang}"
        d.mkdir()
        return d
    monkeypatch.setattr(sr, "_create", create)
    monkeypatch.setattr(sr, "_finish", lambda d, settings: rendered.append(d.name) or {"flags": 0})
    monkeypatch.setattr(sr, "_dir", lambda slug, settings: tmp_path / slug)
    sr.run(path, settings=None, today=date(2026, 10, 5))
    assert made == [(1, "vi"), (1, "en")] and rendered == ["v1vi", "v1en"]
    state = json.loads(sr.state_path(path).read_text(encoding="utf-8"))
    assert state == {"1/vi": "v1vi", "1/en": "v1en"}

    (tmp_path / "v1en" / "final.mp4").write_bytes(b"x")          # 1/en finished; 1/vi was interrupted
    made.clear(), rendered.clear()
    sr.run(path, settings=None, today=date(2026, 10, 5))
    assert made == [] and rendered == ["v1vi"]


def test_finish_refuses_a_folder_the_studio_is_working_on_and_marks_its_own_work(tmp_path, monkeypatch):
    """Seen live: the CLI series and a studio render ran on one folder; one deleted the other's segments."""
    from vidgen import pipeline
    from vidgen.web.jobs import load_job
    d = tmp_path / "v"
    d.mkdir()
    (d / "job.json").write_text(json.dumps({"slug": "v", "kind": "render", "status": "running"}), encoding="utf-8")
    with pytest.raises(pipeline.FolderBusyError):
        sr._finish(d, settings=None)

    (d / "job.json").write_text(json.dumps({"slug": "v", "kind": "render", "status": "error"}), encoding="utf-8")
    (d / "script.json").write_text("{}", encoding="utf-8")
    seen = []
    monkeypatch.setattr(pipeline, "load_brief", lambda out_dir: Brief(topic="t", angles=[
        Angle(style="story", title="T", hook="h", key_points=["k"])]))

    def stages(out_dir, settings, on_stage=lambda n, st: None):
        seen.append(load_job(out_dir).status)                 # the studio sees the folder as busy meanwhile
        on_stage("voice", "run")
        seen.append(load_job(out_dir).current)
    monkeypatch.setattr(pipeline, "run_stages", stages)
    sr._finish(d, settings=None)
    job = load_job(d)
    assert seen == ["running", "voice"] and job.status == "done" and job.kind == "series"
