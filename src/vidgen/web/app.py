"""Local web UI: JSON API over the existing pipeline + static single-page frontend. Binds to 127.0.0.1 only."""

import hashlib
import json
import re
import shutil
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from vidgen import pipeline
from vidgen.config import SECRET_KEYS, Settings, get_settings, update_env_file
from vidgen.fsutil import write_atomic
from vidgen.models import Asset, Script
from vidgen.script.writer import WORDS_PER_SECOND
from vidgen.web.jobs import JobBusyError, JobQueue, JobStatus, load_job, mark_interrupted

STATIC = Path(__file__).parent / "static"
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}$")
URL_KEYS = ("COMFYUI_URL", "OLLAMA_URL")

ScriptRunner = Callable[[Path, Settings], None]
BriefRunner = Callable[[Path, Settings], None]
Splitter = Callable[[str, str, Settings], list[str]]
StageRunner = Callable[..., dict]
SOURCE_PREVIEW_CHARS = 280  # the UI shows a preview; full passages stay on disk


class NewVideo(BaseModel):
    topic: str = Field(min_length=3, max_length=200)
    format: Literal["short", "long"] = "short"
    lang: Literal["vi", "en"] = "vi"
    auto_render: bool = False  # skip the review steps (angle 1, then straight to video)


class Ideas(BaseModel):
    text: str = Field(min_length=3, max_length=4000)
    format: Literal["short", "long"] = "short"
    lang: Literal["vi", "en"] = "vi"
    auto_render: bool = False


class ChooseAngle(BaseModel):
    angle: int
    title: str | None = None
    hook: str | None = None
    key_points: list[str] | None = None
    excluded_urls: list[str] = []
    then_render: bool = False  # "Chọn góc & tạo video luôn": script → voice → visuals → video


class RewriteRequest(BaseModel):
    narration: str | None = Field(default=None, max_length=1000)    # the unsaved text on screen
    instruction: str | None = Field(default=None, max_length=300)   # e.g. "ngắn hơn", "hài hước hơn"


class SwapRequest(BaseModel):
    query: str | None = Field(default=None, max_length=100)  # empty → next saved alternate


class RenderRequest(BaseModel):
    force: Literal["voice", "visuals", "render", "metadata"] | None = None


def video_status(out_dir: Path, job: JobStatus | None) -> str:
    if job and job.status in ("queued", "running", "interrupted", "error"):
        return job.status
    if not (out_dir / "script.json").exists():
        if (out_dir / pipeline.BRIEF_FILE).exists():
            return "brief"  # waiting for the user to pick an angle
        return "error" if job else "empty"
    if not (out_dir / "final.mp4").exists():
        return "review"  # includes "clip swapped, needs re-render" (metadata.json may remain)
    return "done" if (out_dir / "metadata.json").exists() else "rendered"


def last_modified(d: Path) -> float:
    """Newest mtime in a video dir. Files can vanish mid-scan (atomic-write temp files are renamed
    by the worker while the UI polls), and dot-files are those temps — skip both."""
    newest = d.stat().st_mtime
    for p in d.iterdir():
        if p.name.startswith("."):
            continue
        try:
            newest = max(newest, p.stat().st_mtime)
        except FileNotFoundError:
            continue
    return newest


def create_app(settings: Callable[[], Settings] = get_settings,
               script_runner: ScriptRunner = pipeline.write_script,
               stage_runner: StageRunner = pipeline.run_stages,
               jobs: JobQueue | None = None,
               brief_runner: BriefRunner | None = None,
               splitter: Splitter | None = None,
               extend_runner: Callable[[Path, Settings], object] | None = None,
               check_runner: Callable[[Path, Settings], object] | None = None,
               rewriter: Callable[..., dict] | None = None) -> FastAPI:
    app = FastAPI(title="vidgen", docs_url="/api/docs", redoc_url=None)
    # resolved at call time (not as default args) so tests can swap the pipeline functions
    brief_runner = brief_runner or pipeline.write_brief
    splitter = splitter or pipeline.split_topics
    extend_runner = extend_runner or pipeline.extend_script_file
    check_runner = check_runner or pipeline.check_facts
    rewriter = rewriter or pipeline.rewrite_scene
    jobs = jobs or JobQueue()
    app.state.jobs = jobs

    def root() -> Path:
        s = settings()
        return s.path(s.pipeline.output_dir)

    # a job recorded as queued/running by a previous server process was cut off
    if root().exists():
        for d in root().iterdir():
            if d.is_dir():
                mark_interrupted(d)

    def job_of(d: Path) -> JobStatus | None:
        """Live job from this process, else the last one persisted on disk."""
        return jobs.get(d.name) or load_job(d)

    def video_dir(slug: str) -> Path:
        if not SLUG_RE.match(slug):
            raise HTTPException(400, "invalid slug")
        d = root() / slug
        if not (d / "state.json").exists() and not (d / "script.json").exists():
            raise HTTPException(404, f"video {slug} not found")
        return d

    def read_json_model(path: Path, model):
        return model.model_validate_json(path.read_text(encoding="utf-8")) if path.exists() else None

    def read_assets(d: Path) -> list[Asset] | None:
        p = d / "assets.json"
        return pipeline.ASSETS.validate_json(p.read_text(encoding="utf-8")) if p.exists() else None

    def asset_view(a: Asset) -> dict:
        return {"scene_id": a.scene_id, "kind": a.kind, "source": a.source, "author": a.author,
                "url": a.url, "query": a.query, "has_file": bool(a.path),
                "alternates": len(a.alternates), "vision_score": a.vision_score, "key": hashlib.sha1(f"{a.path}|{a.uid}".encode()).hexdigest()[:10]}

    def summary(d: Path) -> dict:
        state = pipeline.load_state(d)
        script = read_json_model(d / "script.json", Script)
        brief = pipeline.load_brief(d)
        angle = brief.chosen_angle() if brief else None
        job = job_of(d)
        return {
            "slug": d.name,
            "topic": state.get("topic", ""),
            "title": script.title if script else (angle.title if angle else state.get("topic", d.name)),
            "format": script.format if script else state.get("format", "short"),
            "lang": script.lang if script else state.get("lang", "vi"),
            "scenes": len(script.scenes) if script else 0,
            "status": video_status(d, job),
            "stage": job.current if job else "",
            "job_kind": job.kind if job else "",
            "has_video": (d / "final.mp4").exists(),
            "updated": last_modified(d),
        }

    def enqueue_brief(d: Path, then_render: bool, regenerate: bool = False) -> None:
        """Angles + sources. With then_render (auto mode) it also picks angle 1 and runs everything."""
        def work(job: JobStatus) -> None:
            job.current = "brief"
            job.save()
            s = settings()
            brief_runner(d, s)
            job.stages["brief"] = "done"
            job.save()
            if then_render:
                pipeline.choose_angle(d, 0)
                job.current = "script"
                job.save()
                script_runner(d, s)
                job.stages["script"] = "done"
                job.save()
                run_render(job, d, s, None)
        jobs.submit(d.name, "brief", work, out_dir=d,
                    options={"then_render": then_render, "regenerate": regenerate})

    def enqueue_script(d: Path, then_render: bool) -> None:
        def work(job: JobStatus) -> None:
            job.current = "script"
            s = settings()
            script_runner(d, s)
            job.stages["script"] = "done"
            job.save()
            if then_render:
                run_render(job, d, s, None)
        jobs.submit(d.name, "script", work, out_dir=d, options={"then_render": then_render})

    def run_render(job: JobStatus, d: Path, s: Settings, force: str | None) -> None:
        def on_stage(name: str, status: str) -> None:
            job.stages[name] = status
            job.current = name if status == "run" else job.current
            job.save()
        stage_runner(d, s, force=force, on_stage=on_stage)

    def submit_render(d: Path, force: str | None) -> JobStatus:
        return jobs.submit(d.name, "render", lambda job: run_render(job, d, settings(), force),
                           out_dir=d, options={"force": force})

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
        enqueue_brief(d, req.auto_render)
        return summary(d)

    @app.post("/api/ideas", status_code=201)
    def create_from_ideas(req: Ideas) -> list[dict]:
        """Pasted notes → one video per detected topic, each starting with a brief job.
        The split is a single quick LLM call (skipped for a one-line idea), so it runs in the request."""
        s = settings()
        try:
            topics = splitter(req.text, req.lang, s)
        except Exception as e:
            raise HTTPException(502, f"could not analyse the ideas: {e}") from e
        created = []
        for topic in topics:
            d = pipeline.init_video(topic, req.format, req.lang, s)
            enqueue_brief(d, req.auto_render)
            created.append(summary(d))
        return created

    @app.get("/api/videos/{slug}")
    def get_video(slug: str) -> dict:
        d = video_dir(slug)
        job = job_of(d)
        meta_path = d / "metadata.json"
        brief = pipeline.load_brief(d)
        if brief is not None:  # passages can be long: send a preview, the UI only needs to judge relevance
            brief_view = brief.model_dump()
            for a in brief_view["angles"]:
                for src in a["sources"]:
                    src["chars"] = len(src["text"])
                    src["text"] = src["text"][:SOURCE_PREVIEW_CHARS]
        info = summary(d)
        actual = pipeline.actual_duration(d)
        return {
            **info,
            "script": read_json_model(d / "script.json", Script),
            "brief": brief_view if brief is not None else None,
            # length indicator: estimate = words / wps in the UI, target range per format, real length once voiced
            "target_seconds": list(settings().preset(info["format"]).target_seconds),
            "wps": WORDS_PER_SECOND[info["lang"]],
            "actual_seconds": actual,
            "factcheck": (json.loads((d / "factcheck.json").read_text(encoding="utf-8"))
                          if (d / "factcheck.json").exists() else None),
            "metadata": json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else None,
            "timings": pipeline.load_state(d).get("timings", {}),
            "job": job.to_dict() if job else None,
            "artifacts": {st.name: (d / st.artifact).exists() for st in pipeline.STAGES},
            # per-scene clips, only while they still match the script (after an edit they'll be re-picked)
            "assets": [asset_view(a) for a in read_assets(d) or []] if actual is not None else None,
        }

    # One writer per video at a time. The job queue covers background jobs; this lock covers the
    # request-thread work (a clip swap downloads for seconds) and the check-then-submit windows.
    locks: dict[str, threading.Lock] = {}
    locks_guard = threading.Lock()

    @contextmanager
    def exclusive(slug: str, check_busy: bool = True):
        with locks_guard:
            lock = locks.setdefault(slug, threading.Lock())
        if not lock.acquire(blocking=False):
            raise HTTPException(409, "video đang được thao tác (ví dụ đang đổi clip) — thử lại sau giây lát")
        try:
            if check_busy and jobs.busy(slug):
                raise HTTPException(409, "a job is running for this video")
            yield
        except JobBusyError as e:  # JobQueue.submit refusing a second job for the same video
            raise HTTPException(409, str(e)) from e
        finally:
            lock.release()

    @app.post("/api/videos/{slug}/brief")
    def choose(slug: str, req: ChooseAngle) -> dict:
        """Pick (and optionally edit) an angle, then write the script from it."""
        d = video_dir(slug)
        with exclusive(slug):
            try:
                pipeline.choose_angle(d, req.angle, req.title, req.hook, req.key_points, req.excluded_urls)
            except ValueError as e:
                raise HTTPException(409, str(e)) from e
            except IndexError as e:
                raise HTTPException(422, str(e)) from e
            enqueue_script(d, then_render=req.then_render)
        return {"ok": True}

    @app.post("/api/videos/{slug}/brief/regenerate")
    def regenerate_brief(slug: str) -> dict:
        """New research + 3 new angles for the same topic (the current script, if any, is kept until
        another angle is chosen)."""
        d = video_dir(slug)
        with exclusive(slug):
            enqueue_brief(d, then_render=False, regenerate=True)
        return {"ok": True}

    def enqueue_task(d: Path, kind: str, fn: Callable[[Path, Settings], object]) -> None:
        """A short LLM task on the video (extend / check) through the same one-at-a-time queue."""
        def work(job: JobStatus) -> None:
            job.current = kind
            job.save()
            fn(d, settings())
        jobs.submit(d.name, kind, work, out_dir=d)

    @app.post("/api/videos/{slug}/extend")
    def extend(slug: str) -> dict:
        """"Kéo dài": add scenes from unused facts toward the target length (then re-checks facts)."""
        d = video_dir(slug)
        if not (d / "script.json").exists():
            raise HTTPException(409, "no script yet")
        with exclusive(slug):
            enqueue_task(d, "extend", extend_runner)
        return {"ok": True}

    @app.post("/api/videos/{slug}/check")
    def check(slug: str) -> dict:
        """Re-run the fact check on the saved script (e.g. after editing sentences)."""
        d = video_dir(slug)
        if not (d / "script.json").exists():
            raise HTTPException(409, "no script yet")
        with exclusive(slug):
            enqueue_task(d, "check", check_runner)
        return {"ok": True}

    @app.post("/api/videos/{slug}/scenes/{scene_id}/rewrite")
    def rewrite(slug: str, scene_id: int, req: RewriteRequest) -> dict:
        """AI rewrite of one scene, returned (not saved): the UI puts it in the draft to accept or undo."""
        d = video_dir(slug)
        if not (d / "script.json").exists():
            raise HTTPException(409, "no script yet")
        try:
            return rewriter(d, settings(), scene_id, req.narration, req.instruction)
        except KeyError as e:
            raise HTTPException(404, str(e)) from e
        except Exception as e:  # LLM down / invalid output
            raise HTTPException(502, f"AI không viết lại được: {e}") from e

    @app.post("/api/render-all")
    def render_all() -> dict:
        """Queue a render for every video whose script is ready but not rendered yet."""
        r = root()
        queued = []
        for d in (sorted(r.iterdir()) if r.exists() else []):
            if d.is_dir() and SLUG_RE.match(d.name) and video_status(d, job_of(d)) == "review":
                try:
                    with exclusive(d.name, check_busy=False):
                        submit_render(d, None)
                    queued.append(d.name)
                except HTTPException:
                    continue  # busy or locked: skip it, the rest still get queued
        return {"queued": queued}

    @app.put("/api/videos/{slug}/script")
    def save_script(slug: str, body: dict) -> dict:
        d = video_dir(slug)
        try:
            script = Script.model_validate(body)
        except ValidationError as e:
            raise HTTPException(422, e.errors(include_url=False)) from e
        # renumber so ids stay 1..n after the user adds/removes scenes
        script.scenes = [sc.model_copy(update={"id": i}) for i, sc in enumerate(script.scenes, 1)]
        with exclusive(slug):
            write_atomic(d / "script.json", script.model_dump_json(indent=2))
        return {"ok": True, "scenes": len(script.scenes)}

    @app.post("/api/videos/{slug}/render")
    def render(slug: str, req: RenderRequest) -> dict:
        d = video_dir(slug)
        if not (d / "script.json").exists():
            raise HTTPException(409, "no script yet")
        with exclusive(slug, check_busy=False):
            job = submit_render(d, req.force)
        return job.to_dict()

    @app.post("/api/videos/{slug}/resume")
    def resume(slug: str) -> dict:
        """Re-submit an interrupted job. Finished stages are skipped by the pipeline itself."""
        d = video_dir(slug)
        job = job_of(d)
        if job is None or job.status != "interrupted":
            raise HTTPException(409, "nothing to resume")
        with exclusive(slug, check_busy=False):
            if job.kind == "render":
                submit_render(d, None)  # never re-apply force: that would redo finished stages
            elif job.kind == "check":
                enqueue_task(d, "check", check_runner)
            elif job.kind == "extend":
                # re-running could add scenes twice if the first run already saved; let the user decide
                job.status = "done"
                job.save()
            elif job.kind == "brief" and (job.options.get("regenerate")
                                          or not (d / pipeline.BRIEF_FILE).exists()):
                # a cut-off regeneration left the OLD angles on disk: run it again
                enqueue_brief(d, then_render=bool(job.options.get("then_render")),
                              regenerate=bool(job.options.get("regenerate")))
            elif job.kind == "brief" and not job.options.get("then_render"):
                job.status = "done"  # brief.json was written before the cut: nothing left to run
                job.save()
            elif job.stages.get("script") == "done" and (d / "script.json").exists():
                # auto job cut off after its script: only the render part is left (no second LLM call)
                submit_render(d, None)
            else:  # script job, or an auto brief that got past the brief itself
                brief = pipeline.load_brief(d)
                if brief is not None and brief.chosen is None:
                    pipeline.choose_angle(d, 0)  # auto mode always takes angle 1
                enqueue_script(d, then_render=bool(job.options.get("then_render")))
        return {"ok": True}

    @app.post("/api/videos/{slug}/script/regenerate")
    def regenerate_script(slug: str) -> dict:
        d = video_dir(slug)
        with exclusive(slug, check_busy=False):
            enqueue_script(d, then_render=False)
        return {"ok": True}

    @app.post("/api/videos/{slug}/scenes/{scene_id}/swap")
    def swap(slug: str, scene_id: int, req: SwapRequest) -> dict:
        """Replace one scene's clip; only the final render is dropped (other clips, voice, metadata stay).
        Holds the video's lock for the whole download so no render or second swap can interleave."""
        from vidgen.visuals.selector import SwapError

        d = video_dir(slug)
        with exclusive(slug):
            if not (d / "assets.json").exists() or pipeline.actual_duration(d) is None:
                raise HTTPException(409, "render the video first (clips exist only after the visuals step)")
            try:
                asset = pipeline.swap_clip(d, settings(), scene_id, (req.query or "").strip() or None)
            except KeyError as e:
                raise HTTPException(404, str(e)) from e
            except SwapError as e:
                raise HTTPException(422, str(e)) from e
        return asset_view(asset)

    @app.delete("/api/videos/{slug}")
    def delete_video(slug: str) -> dict:
        d = video_dir(slug)
        with exclusive(slug):
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

    @app.get("/media/{slug}/scenes/{scene_id}.jpg")
    def media_scene_thumb(slug: str, scene_id: int) -> FileResponse:
        """Small frame of a scene's current clip. Named after the clip (not mtime-checked): swapped files
        are hard links to the download cache, so their mtime can be older than an existing thumbnail."""
        from vidgen import ffmpeg

        d = video_dir(slug)
        asset = next((a for a in (read_assets(d) or []) if a.scene_id == scene_id), None)
        src = (d / asset.path).resolve() if asset and asset.path else None
        if src is None or not src.is_relative_to(d.resolve()) or not src.exists():
            raise HTTPException(404)
        key = hashlib.sha1(f"{asset.path}|{asset.uid}".encode()).hexdigest()[:10]
        thumb = d / "thumbs" / f"scene_{scene_id:03d}_{key}.jpg"
        if not thumb.exists():
            thumb.parent.mkdir(exist_ok=True)
            try:  # seek into videos for a representative frame; very short clips fall back to frame 0
                for seek in ((["-ss", "0.5"] if asset.kind == "video" else []), []):
                    ffmpeg.run([*seek, "-i", str(src), "-frames:v", "1", "-vf", "scale=240:-2", str(thumb)])
                    if thumb.exists():
                        break
            except (ffmpeg.FFmpegError, OSError):
                pass
            if not thumb.exists():
                raise HTTPException(404)
        return FileResponse(thumb, media_type="image/jpeg")

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

    @app.middleware("http")
    async def revalidate_static(request, call_next):
        """Static UI files: always revalidate (cheap 304 via ETag) so an updated app.js is used at once."""
        response = await call_next(request)
        if not request.url.path.startswith(("/api/", "/media/")):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
    return app
