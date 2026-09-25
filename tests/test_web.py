import json

import pytest
from fastapi.testclient import TestClient

from vidgen import config
from vidgen.config import get_settings
from vidgen.fsutil import write_atomic
from vidgen.models import Scene, Script
from vidgen.web.app import create_app, video_status
from vidgen.web.jobs import JobQueue, JobStatus

SCRIPT = Script(title="Bermuda", hook="h", lang="vi", format="short",
                scenes=[Scene(id=1, narration="Một.", visual_query="ocean"),
                        Scene(id=2, narration="Hai.", visual_query="ship")])


@pytest.fixture
def env(tmp_path, monkeypatch):
    s = get_settings().model_copy(deep=True)
    s.pipeline.output_dir = tmp_path / "output"
    calls = {"script": 0, "render": []}

    def fake_script(out_dir, settings):
        calls["script"] += 1
        write_atomic(out_dir / "script.json", SCRIPT.model_dump_json())  # real writers are atomic too

    def fake_stages(out_dir, settings, force=None, on_stage=lambda n, st: None):
        calls["render"].append(force)
        for name in ("voice", "visuals", "render"):
            on_stage(name, "run")
            on_stage(name, "done 0.1s")
        (out_dir / "final.mp4").write_bytes(b"\x00" * 64)
        return {}

    jobs = JobQueue()
    app = create_app(settings=lambda: s, script_runner=fake_script, stage_runner=fake_stages, jobs=jobs)
    return TestClient(app), jobs, calls, s


def create(client, jobs, **kw):
    r = client.post("/api/videos", json={"topic": "Bí ẩn tam giác Bermuda", **kw})
    assert r.status_code == 201, r.text
    jobs.wait_idle()
    return r.json()["slug"]


def test_create_lists_and_reviews(env):
    client, jobs, calls, _ = env
    slug = create(client, jobs)
    videos = client.get("/api/videos").json()
    assert [v["slug"] for v in videos] == [slug]
    assert videos[0]["status"] == "review" and videos[0]["title"] == "Bermuda"
    detail = client.get(f"/api/videos/{slug}").json()
    assert len(detail["script"]["scenes"]) == 2 and detail["job"]["status"] == "done"
    assert calls["render"] == []  # review step: no render until asked


def test_auto_render_runs_straight_through(env):
    client, jobs, calls, _ = env
    slug = create(client, jobs, auto_render=True)
    d = client.get(f"/api/videos/{slug}").json()
    assert d["status"] == "rendered" and d["has_video"]
    assert d["job"]["stages"]["render"].startswith("done")


def test_save_script_renumbers_and_validates(env):
    client, jobs, _, s = env
    slug = create(client, jobs)
    body = SCRIPT.model_dump()
    body["scenes"] = [dict(body["scenes"][1], id=9), dict(body["scenes"][0], id=4)]
    assert client.put(f"/api/videos/{slug}/script", json=body).status_code == 200
    saved = json.loads((s.pipeline.output_dir / slug / "script.json").read_text(encoding="utf-8"))
    assert [sc["id"] for sc in saved["scenes"]] == [1, 2]
    assert saved["scenes"][0]["narration"] == "Hai."
    body["scenes"] = []
    assert client.put(f"/api/videos/{slug}/script", json=body).status_code == 422


def test_render_with_force_and_media(env):
    client, jobs, calls, _ = env
    slug = create(client, jobs)
    assert client.post(f"/api/videos/{slug}/render", json={"force": "render"}).status_code == 200
    jobs.wait_idle()
    assert calls["render"] == ["render"]
    r = client.get(f"/media/{slug}/final.mp4", headers={"Range": "bytes=0-9"})
    assert r.status_code == 206 and len(r.content) == 10
    assert client.post(f"/api/videos/{slug}/render", json={"force": "bogus"}).status_code == 422


def test_busy_video_rejects_second_job_and_edits(env):
    client, jobs, _, _ = env
    slug = create(client, jobs)
    import threading
    gate = threading.Event()
    jobs.submit(slug, "render", lambda job: gate.wait(5))
    try:
        assert client.post(f"/api/videos/{slug}/render", json={}).status_code == 409
        assert client.put(f"/api/videos/{slug}/script", json=SCRIPT.model_dump()).status_code == 409
        assert client.delete(f"/api/videos/{slug}").status_code == 409
    finally:
        gate.set()
        jobs.wait_idle()


def test_failed_job_surfaces_error(env, tmp_path):
    client, jobs, _, s = env

    def boom(out_dir, settings):
        raise RuntimeError("All LLM providers failed")

    app = create_app(settings=lambda: s, script_runner=boom, jobs=jobs)
    c2 = TestClient(app)
    slug = create(c2, jobs)
    d = c2.get(f"/api/videos/{slug}").json()
    assert d["status"] == "error" and "LLM" in d["job"]["error"]


def test_delete_and_slug_validation(env):
    client, jobs, _, s = env
    slug = create(client, jobs)
    assert client.get("/api/videos/..%2F..%2Fetc").status_code in (400, 404)
    assert client.get("/api/videos/UPPER").status_code == 400
    assert client.delete(f"/api/videos/{slug}").json() == {"ok": True}
    assert not (s.pipeline.output_dir / slug).exists()
    assert client.get(f"/api/videos/{slug}").status_code == 404


def test_settings_masks_secrets_and_writes_env(env, tmp_path, monkeypatch):
    client, _, _, s = env
    env_file = tmp_path / ".env"
    env_file.write_text("# keep me\nGEMINI_API_KEY=old\n", encoding="utf-8")
    real = config.update_env_file
    monkeypatch.setattr("vidgen.web.app.update_env_file", lambda v: real(v, env_file))
    s.secrets.gemini_api_key = "AIzaSyVerySecret1234"
    view = client.get("/api/settings").json()
    assert view["GEMINI_API_KEY"] == {"set": True, "hint": "…1234"}
    assert "VerySecret" not in json.dumps(view)

    assert client.put("/api/settings", json={"PEXELS_API_KEY": "px"}).status_code == 200
    text = env_file.read_text(encoding="utf-8")
    assert "# keep me" in text and "GEMINI_API_KEY=old" in text and "PEXELS_API_KEY=px" in text
    assert client.put("/api/settings", json={"EVIL": "x"}).status_code == 400


def test_video_status_rules(tmp_path):
    assert video_status(tmp_path, None) == "empty"
    assert video_status(tmp_path, JobStatus("s", "script", status="running")) == "running"
    (tmp_path / "script.json").write_text("{}")
    assert video_status(tmp_path, None) == "review"
    (tmp_path / "final.mp4").write_text("x")
    assert video_status(tmp_path, None) == "rendered"
    (tmp_path / "metadata.json").write_text("{}")
    assert video_status(tmp_path, None) == "done"


def test_static_index_served(env):
    client, *_ = env
    r = client.get("/")
    assert r.status_code == 200 and "vidgen" in r.text
    assert client.get("/app.js").status_code == 200


# --- phase: job persistence -----------------------------------------------------------------
def test_job_persisted_to_disk(env):
    client, jobs, _, s = env
    slug = create(client, jobs, auto_render=True)
    saved = json.loads((s.pipeline.output_dir / slug / "job.json").read_text(encoding="utf-8"))
    assert saved["status"] == "done" and saved["stages"]["render"].startswith("done")
    assert saved["options"] == {"then_render": True}


def test_restart_marks_running_job_interrupted_and_resume_finishes(env):
    client, jobs, calls, s = env
    slug = create(client, jobs)
    d = s.pipeline.output_dir / slug
    # simulate a server that died mid-render
    job = json.loads((d / "job.json").read_text(encoding="utf-8"))
    job.update(kind="render", status="running", current="visuals", options={"force": "voice"})
    (d / "job.json").write_text(json.dumps(job), encoding="utf-8")

    from vidgen.web.app import create_app
    fresh_jobs = JobQueue()
    stages_seen = []

    def fake_stages(out_dir, settings, force=None, on_stage=lambda n, st: None):
        stages_seen.append(force)
        (out_dir / "final.mp4").write_bytes(b"x")
        return {}

    app2 = TestClient(create_app(settings=lambda: s, stage_runner=fake_stages, jobs=fresh_jobs))
    v = app2.get(f"/api/videos/{slug}").json()
    assert v["status"] == "interrupted" and v["job"]["current"] == "visuals"
    assert app2.post(f"/api/videos/{slug}/resume").status_code == 200
    fresh_jobs.wait_idle()
    assert stages_seen == [None]  # resume never re-applies force
    assert app2.get(f"/api/videos/{slug}").json()["status"] == "rendered"
    assert app2.post(f"/api/videos/{slug}/resume").status_code == 409  # nothing left to resume


def test_interrupted_script_job_resumes_with_its_options(env):
    client, jobs, calls, s = env
    slug = create(client, jobs)
    d = s.pipeline.output_dir / slug
    (d / "job.json").write_text(json.dumps({"slug": slug, "kind": "script", "status": "queued",
                                            "options": {"then_render": True}}), encoding="utf-8")
    from vidgen.web.app import create_app
    fresh = JobQueue()
    rendered = []
    app2 = TestClient(create_app(settings=lambda: s,
                                 script_runner=lambda out_dir, st: (out_dir / "script.json").write_text(
                                     SCRIPT.model_dump_json(), encoding="utf-8"),
                                 stage_runner=lambda *a, **k: rendered.append(1) or {}, jobs=fresh))
    assert app2.post(f"/api/videos/{slug}/resume").status_code == 200
    fresh.wait_idle()
    assert rendered == [1]


def test_mark_interrupted_ignores_finished_jobs(tmp_path):
    from vidgen.web.jobs import load_job, mark_interrupted

    (tmp_path / "job.json").write_text(json.dumps({"slug": "x", "kind": "render", "status": "done"}))
    assert mark_interrupted(tmp_path) is False and load_job(tmp_path).status == "done"
    (tmp_path / "job.json").write_text("{broken")
    assert load_job(tmp_path) is None
