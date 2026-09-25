"""Local web UI: JSON API over the existing pipeline + static single-page frontend. Binds to 127.0.0.1 only."""

import json
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from vidgen import pipeline
from vidgen.config import SECRET_KEYS, Settings, get_settings, update_env_file
from vidgen.models import Script
from vidgen.web.jobs import JobQueue, JobStatus

STATIC = Path(__file__).parent / "static"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
URL_KEYS = ("COMFYUI_URL", "OLLAMA_URL")

ScriptRunner = Callable[[Path, Settings], None]
StageRunner = Callable[..., dict]


class NewVideo(BaseModel):
    topic: str = Field(min_length=3, max_length=200)
    format: Literal["short", "long"] = "short"
    lang: Literal["vi", "en"] = "vi"
    auto_render: bool = False  # skip the review step


class RenderRequest(BaseModel):
    force: Literal["voice", "visuals", "render", "metadata"] | None = None


def video_status(out_dir: Path, job: JobStatus | None) -> str:
    if job and job.status in ("queued", "running"):
        return job.status
    if job and job.status == "error":
        return "error"
    if not (out_dir / "script.json").exists():
        return "error" if job else "empty"
    if (out_dir / "metadata.json").exists():
        return "done"
    if (out_dir / "final.mp4").exists():
        return "rendered"
    return "review"


def create_app(settings: Callable[[], Settings] = get_settings,
               script_runner: ScriptRunner = pipeline.write_script,
               stage_runner: StageRunner = pipeline.run_stages,
               jobs: JobQueue | None = None) -> FastAPI:
    app = FastAPI(title="vidgen", docs_url="/api/docs", redoc_url=None)
    jobs = jobs or JobQueue()
    app.state.jobs = jobs

    def root() -> Path:
        s = settings()
        return s.path(s.pipeline.output_dir)

    def video_dir(slug: str) -> Path:
        if not SLUG_RE.match(slug):
            raise HTTPException(400, "invalid slug")
        d = root() / slug
        if not (d / "state.json").exists() and not (d / "script.json").exists():
            raise HTTPException(404, f"video {slug} not found")
        return d

    def read_json_model(path: Path, model):
        return model.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None

    def summary(d: Path) -> dict:
        state = pipeline.load_state(d)
        script = read_json_model(d / "script.json", Script)
        job = jobs.get(d.name)
        return {
            "slug": d.name,
            "topic": state.get("topic", ""),
            "title": script.title if script else state.get("topic", d.name),
            "format": script.format if script else state.get("format", "short"),
            "lang": script.lang if script else state.get("lang", "vi"),
            "scenes": len(script.scenes) if script else 0,
            "status": video_status(d, job),
            "stage": job.current if job else "",
            "has_video": (d / "final.mp4").exists(),
            "updated": max((p.stat().st_mtime for p in d.iterdir()), default=d.stat().st_mtime),
        }

    def enqueue_script(d: Path, then_render: bool) -> None:
        def work(job: JobStatus) -> None:
            job.current = "script"
            s = settings()
            script_runner(d, s)
            job.stages["script"] = "done"
            if then_render:
                run_render(job, d, s, None)
        jobs.submit(d.name, "script", work)

    def run_render(job: JobStatus, d: Path, s: Settings, force: str | None) -> None:
        def on_stage(name: str, status: str) -> None:
            job.stages[name] = status
            job.current = name if status == "run" else job.current
        stage_runner(d, s, force=force, on_stage=on_stage)

    # --- videos -------------------------------------------------------------------------
    @app.get("/api/videos")
    def list_videos() -> list[dict]:
        r = root()
        if not r.exists():
            return []
        dirs = [d for d in r.iterdir() if d.is_dir() and SLUG_RE.match(d.name)
                and ((d / "state.json").exists() or (d / "script.json").exists())]
        return sorted((summary(d) for d in dirs), key=lambda v: v["updated"], reverse=True)

    @app.post("/api/videos", status_code=201)
    def create_video(req: NewVideo) -> dict:
        d = pipeline.init_video(req.topic.strip(), req.format, req.lang, settings())
        enqueue_script(d, req.auto_render)
        return summary(d)

    @app.get("/api/videos/{slug}")
    def get_video(slug: str) -> dict:
        d = video_dir(slug)
        job = jobs.get(slug)
        meta_path = d / "metadata.json"
        return {
            **summary(d),
            "script": read_json_model(d / "script.json", Script),
            "metadata": json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None,
            "timings": pipeline.load_state(d).get("timings", {}),
            "job": job.to_dict() if job else None,
            "artifacts": {st.name: (d / st.artifact).exists() for st in pipeline.STAGES},
        }

    @app.put("/api/videos/{slug}/script")
    def save_script(slug: str, body: dict) -> dict:
        d = video_dir(slug)
        if jobs.busy(slug):
            raise HTTPException(409, "a job is running for this video")
        try:
            script = Script.model_validate(body)
        except ValidationError as e:
            raise HTTPException(422, e.errors(include_url=False)) from e
        # renumber so ids stay 1..n after the user adds/removes scenes
        script.scenes = [sc.model_copy(update={"id": i}) for i, sc in enumerate(script.scenes, 1)]
        (d / "script.json").write_text(script.model_dump_json(indent=2), encoding="utf-8")
        return {"ok": True, "scenes": len(script.scenes)}

    @app.post("/api/videos/{slug}/render")
    def render(slug: str, req: RenderRequest) -> dict:
        d = video_dir(slug)
        if not (d / "script.json").exists():
            raise HTTPException(409, "no script yet")
        try:
            job = jobs.submit(slug, "render", lambda job: run_render(job, d, settings(), req.force))
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return job.to_dict()

    @app.post("/api/videos/{slug}/script/regenerate")
    def regenerate_script(slug: str) -> dict:
        d = video_dir(slug)
        try:
            enqueue_script(d, then_render=False)
        except RuntimeError as e:
            raise HTTPException(409, str(e)) from e
        return {"ok": True}

    @app.delete("/api/videos/{slug}")
    def delete_video(slug: str) -> dict:
        d = video_dir(slug)
        if jobs.busy(slug):
            raise HTTPException(409, "a job is running for this video")
        shutil.rmtree(d)
        return {"ok": True}

    # --- media ----------------------------------------------------------------------------
    @app.get("/media/{slug}/final.mp4")
    def media_video(slug: str) -> FileResponse:
        f = video_dir(slug) / "final.mp4"
        if not f.exists():
            raise HTTPException(404)
        return FileResponse(f, media_type="video/mp4", headers={"Cache-Control": "no-cache"})

    @app.get("/media/{slug}/thumb.jpg")
    def media_thumb(slug: str) -> FileResponse:
        from vidgen import ffmpeg

        d = video_dir(slug)
        video, thumb = d / "final.mp4", d / "thumb.jpg"
        if not video.exists():
            raise HTTPException(404)
        if not thumb.exists() or thumb.stat().st_mtime < video.stat().st_mtime:
            ffmpeg.run(["-ss", "1", "-i", str(video), "-frames:v", "1", "-vf", "scale=480:-2", str(thumb)])
        return FileResponse(thumb, media_type="image/jpeg", headers={"Cache-Control": "no-cache"})

    # --- settings ---------------------------------------------------------------------------
    @app.get("/api/doctor")
    def doctor() -> list[dict]:
        from vidgen.doctor import run_checks

        return [c.__dict__ for c in run_checks(settings())]

    @app.get("/api/settings")
    def get_settings_view() -> dict:
        sec = settings().secrets
        out = {}
        for key in SECRET_KEYS:
            value = getattr(sec, key.lower())
            if key in URL_KEYS:
                out[key] = {"set": bool(value), "value": value}
            else:  # never send secrets back, only a hint that one is stored
                out[key] = {"set": bool(value), "hint": f"…{value[-4:]}" if len(value) > 8 else ""}
        return out

    @app.put("/api/settings")
    def put_settings(body: dict[str, str]) -> dict:
        unknown = set(body) - set(SECRET_KEYS)
        if unknown:
            raise HTTPException(400, f"unknown keys: {', '.join(sorted(unknown))}")
        update_env_file({k: v for k, v in body.items() if v is not None})
        return get_settings_view()

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "time": time.time()}

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
