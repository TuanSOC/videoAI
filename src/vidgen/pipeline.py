"""Resumable stage runner. Each stage writes one artifact in output/<slug>/; existing artifacts are skipped.

Editing script.json between runs (the review step) invalidates everything downstream.
"""

import hashlib
import json
import logging
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from pydantic import TypeAdapter
from slugify import slugify

from vidgen.config import Settings
from vidgen.fsutil import write_atomic
from vidgen.models import Asset, Brief, Script, Timeline

log = logging.getLogger(__name__)
ASSETS = TypeAdapter(list[Asset])


@dataclass
class Stage:
    name: str
    artifact: str
    extra: tuple[str, ...]  # other files/dirs the stage owns, removed on invalidation
    run: Callable[["Job"], None]


@dataclass
class Job:
    out_dir: Path
    settings: Settings
    topic: str = ""

    def read_script(self) -> Script:
        return Script.model_validate_json((self.out_dir / "script.json").read_text(encoding="utf-8"))

    def read_timeline(self) -> Timeline:
        return Timeline.model_validate_json((self.out_dir / "timeline.json").read_text(encoding="utf-8"))

    def read_assets(self) -> list[Asset]:
        return ASSETS.validate_json((self.out_dir / "assets.json").read_text(encoding="utf-8"))


# --- stage bodies (imports are lazy so `doctor`/`--help` stay fast) ---------------------
def _script(job: Job, fmt: str = "short", lang: str = "vi") -> None:
    from vidgen.script.llm import default_chain
    from vidgen.script.research import research
    from vidgen.script.writer import generate

    s = job.settings
    llm = default_chain(s)
    brief = load_brief(job.out_dir)
    angle = brief.chosen_angle() if brief else None
    if angle is not None:
        sources = brief.chosen_sources()
    else:  # no brief (CLI legacy / older videos): research straight from the topic
        sources = research(job.topic, lang, llm) if s.pipeline.research else []
    if sources:  # kept for the reviewer: what the script's facts are supposed to come from
        write_atomic(job.out_dir / "sources.md", "\n\n---\n\n".join(
            f"# {src.title}\n{src.url}\n\n{src.text}" for src in sources))
    else:
        (job.out_dir / "sources.md").unlink(missing_ok=True)
    script = generate(job.topic, fmt, lang, s.preset(fmt), llm, sources, angle)
    write_atomic(job.out_dir / "script.json", script.model_dump_json(indent=2))


def _voice(job: Job) -> None:
    from vidgen.voice.builder import generate_voice

    generate_voice(job.read_script(), job.out_dir, job.settings)


def _visuals(job: Job) -> None:
    from vidgen.visuals.selector import source_visuals

    script = job.read_script()
    assets = source_visuals(script, job.read_timeline(), job.settings.preset(script.format),
                            job.out_dir, job.settings)
    write_atomic(job.out_dir / "assets.json", ASSETS.dump_json(assets, indent=2))


def _render(job: Job) -> None:
    from vidgen.assemble.render import render_video

    script = job.read_script()
    assets = job.read_assets()
    render_video(script, job.read_timeline(), assets, job.settings.preset(script.format),
                 job.out_dir, seed=job.out_dir.name)
    meta_path = job.out_dir / "metadata.json"
    if meta_path.exists():  # re-render after a clip swap: keep the LLM text, refresh footage credits
        from vidgen.metadata import Metadata, rebuild_description, save_metadata

        meta = Metadata.model_validate_json(meta_path.read_text(encoding="utf-8"))
        save_metadata(rebuild_description(meta, script, assets), meta_path)


def _metadata(job: Job) -> None:
    from vidgen.metadata import generate_metadata, save_metadata
    from vidgen.script.llm import default_chain

    meta = generate_metadata(job.read_script(), job.read_assets(), default_chain(job.settings))
    save_metadata(meta, job.out_dir / "metadata.json")


# script.json is created by create_script() and owned by the user after review; it is never
# regenerated or deleted by the runner.
STAGES = [
    Stage("voice", "timeline.json", ("voice", "voice.wav"), _voice),
    Stage("visuals", "assets.json", ("visuals", "thumbs"), _visuals),
    Stage("render", "final.mp4", ("segments", "subs.ass", "fonts"), _render),
    Stage("metadata", "metadata.json", (), _metadata),
]
STAGE_NAMES = [st.name for st in STAGES]


# --- state -------------------------------------------------------------------------------
def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16] if path.exists() else ""


def _load_state(out_dir: Path) -> dict:
    p = out_dir / "state.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_state(out_dir: Path, state: dict) -> None:
    write_atomic(out_dir / "state.json", json.dumps(state, indent=2, ensure_ascii=False))


def invalidate_from(out_dir: Path, stage: str) -> list[str]:
    """Delete artifacts of `stage` and every later stage. Returns names of stages cleared."""
    cleared = []
    for st in STAGES[STAGE_NAMES.index(stage):]:
        removed = False
        for name in (st.artifact, *st.extra):
            p = out_dir / name
            if p.is_dir():
                shutil.rmtree(p)
                removed = True
            elif p.exists():
                p.unlink()
                removed = True
        if removed:
            cleared.append(st.name)
    return cleared


# --- public API ----------------------------------------------------------------------------
def new_output_dir(topic: str, s: Settings, today: date | None = None) -> Path:
    base = slugify(topic, max_length=50) or "video"
    stem = f"{base}-{(today or date.today()):%y%m%d}"
    root = s.path(s.pipeline.output_dir)
    out, n = root / stem, 2
    while out.exists():
        out, n = root / f"{stem}-{n}", n + 1
    out.mkdir(parents=True)
    return out


def init_video(topic: str, fmt: str, lang: str, s: Settings) -> Path:
    """Create the output dir and record the request, before any slow work (the web UI lists it at once)."""
    out_dir = new_output_dir(topic, s)
    _save_state(out_dir, {"topic": topic, "format": fmt, "lang": lang})
    return out_dir


def write_script(out_dir: Path, s: Settings) -> None:
    """(Re)generate script.json from the topic/format/lang recorded by init_video.
    Everything built from a previous script (voice, clips, video, metadata) is dropped first:
    the new hash is recorded below, so run_stages could not detect the change on its own."""
    invalidate_from(out_dir, "voice")
    state = _load_state(out_dir)
    t = time.time()
    _script(Job(out_dir, s, state["topic"]), state["format"], state["lang"])
    _replace_script_done(out_dir, s, state, t)


def _replace_script_done(out_dir: Path, s: Settings, state: dict, started: float) -> None:
    """Bookkeeping after script.json was (re)written by the pipeline: record its hash, then fact-check."""
    state["script_hash"] = _hash(out_dir / "script.json")
    state.setdefault("timings", {})["script"] = round(time.time() - started, 1)
    _save_state(out_dir, state)
    check_facts(out_dir, s)


def _sources_and_angle(out_dir: Path):
    brief = load_brief(out_dir)
    return (brief.chosen_sources(), brief.chosen_angle()) if brief else ([], None)


def check_facts(out_dir: Path, s: Settings) -> None:
    """Flag scenes the sources don't support → factcheck.json. Never fails the caller: a check that
    can't run just leaves no flags (the UI then says it wasn't checked)."""
    from vidgen.script.factcheck import FILE, FactCheck, fact_check
    from vidgen.script.llm import default_chain

    sources, _ = _sources_and_angle(out_dir)
    try:
        result = fact_check(Job(out_dir, s).read_script(), sources, default_chain(s))
    except Exception as e:
        log.warning("fact check failed: %s", e)
        result = FactCheck(checked=False)
    write_atomic(out_dir / FILE, result.model_dump_json(indent=2))


def extend_script_file(out_dir: Path, s: Settings) -> int:
    """"Kéo dài": add scenes toward the target length. Returns words added; raises if none could be."""
    from vidgen.script.llm import default_chain
    from vidgen.script.writer import extend_script

    script = Job(out_dir, s).read_script()
    sources, angle = _sources_and_angle(out_dir)
    t = time.time()
    longer = extend_script(script, s.preset(script.format), default_chain(s), sources, angle)
    if longer is None:
        raise ValueError("Không thêm được nội dung (kịch bản đã đủ dài hoặc nguồn không còn dữ kiện mới)")
    invalidate_from(out_dir, "voice")
    write_atomic(out_dir / "script.json", longer.model_dump_json(indent=2))
    _replace_script_done(out_dir, s, _load_state(out_dir), t)
    return longer.word_count - script.word_count


def rewrite_scene(out_dir: Path, s: Settings, scene_id: int, narration: str | None = None,
                  instruction: str | None = None) -> dict:
    """One scene rewritten by the LLM, with its neighbours and the sources as context. Nothing is saved:
    the UI drops the result into the unsaved draft for the user to accept (Lưu) or undo."""
    from vidgen.script.llm import default_chain
    from vidgen.script.writer import rewrite_one

    script = Job(out_dir, s).read_script()
    idx = next((i for i, sc in enumerate(script.scenes) if sc.id == scene_id), None)
    if idx is None:
        raise KeyError(f"scene {scene_id} not found")
    sources, angle = _sources_and_angle(out_dir)
    scene = script.scenes[idx]
    if narration:
        scene = scene.model_copy(update={"narration": narration})
    prev = script.scenes[idx - 1].narration if idx > 0 else ""
    nxt = script.scenes[idx + 1].narration if idx + 1 < len(script.scenes) else ""
    new = rewrite_one(script, scene, prev, nxt, instruction, sources, angle, default_chain(s))
    return {"narration": new.narration, "visual_query": new.visual_query}


BRIEF_FILE = "brief.json"


def load_brief(out_dir: Path) -> Brief | None:
    p = out_dir / BRIEF_FILE
    return Brief.model_validate_json(p.read_text(encoding="utf-8")) if p.exists() else None


def split_topics(text: str, lang: str, s: Settings) -> list[str]:
    """Rough pasted notes → clean topics (one video each)."""
    from vidgen.script.brief import split_ideas
    from vidgen.script.llm import default_chain

    return split_ideas(text, lang, default_chain(s))


def write_brief(out_dir: Path, s: Settings) -> None:
    """3 angles with sources for the topic recorded by init_video → brief.json."""
    from vidgen.script.brief import make_brief
    from vidgen.script.llm import default_chain

    state = _load_state(out_dir)
    t = time.time()
    brief = make_brief(state["topic"], state["lang"], state["format"], default_chain(s),
                       research=s.pipeline.research)
    write_atomic(out_dir / BRIEF_FILE, brief.model_dump_json(indent=2))
    state.setdefault("timings", {})["brief"] = round(time.time() - t, 1)
    _save_state(out_dir, state)


def choose_angle(out_dir: Path, index: int, title: str | None = None, hook: str | None = None,
                 key_points: list[str] | None = None, excluded_urls: list[str] | None = None) -> Brief:
    """Record the user's pick (and edits) in brief.json; the next script generation follows it."""
    brief = load_brief(out_dir)
    if brief is None:
        raise ValueError("no brief for this video")
    if not 0 <= index < len(brief.angles):
        raise IndexError(f"angle {index} out of range (0-{len(brief.angles) - 1})")
    angle = brief.angles[index]
    if title and title.strip():
        angle.title = title.strip()
    if hook and hook.strip():
        angle.hook = hook.strip()
    if key_points is not None:
        points = [p.strip() for p in key_points if p.strip()]
        if points:
            angle.key_points = points
    if excluded_urls is not None:
        brief.excluded_urls = excluded_urls
    brief.chosen = index
    write_atomic(out_dir / BRIEF_FILE, brief.model_dump_json(indent=2))
    return brief


def create_script(topic: str, fmt: str, lang: str, s: Settings, angle: int = 0) -> Path:
    """CLI path: brief → auto-pick `angle` → script."""
    out_dir = init_video(topic, fmt, lang, s)
    write_brief(out_dir, s)
    choose_angle(out_dir, min(angle, len(load_brief(out_dir).angles) - 1))
    write_script(out_dir, s)
    return out_dir


def load_state(out_dir: Path) -> dict:
    return _load_state(out_dir)


def invalidate_stage(out_dir: Path, stage: str) -> None:
    """Remove ONE stage's outputs (unlike invalidate_from, later stages such as metadata survive)."""
    st = STAGES[STAGE_NAMES.index(stage)]
    for name in (st.artifact, *st.extra):
        p = out_dir / name
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()


def swap_clip(out_dir: Path, s: Settings, scene_id: int, query: str | None = None) -> Asset:
    """Replace one scene's visual (next alternate, or a search for `query`), then drop only the
    rendered video: re-rendering reuses every other clip and refreshes metadata credits without the LLM."""
    from vidgen.visuals.selector import Selector, llm_judge, stock_clients, video_subject

    script, assets = Job(out_dir, s).read_script(), Job(out_dir, s).read_assets()
    scene = next((sc for sc in script.scenes if sc.id == scene_id), None)
    current = next((a for a in assets if a.scene_id == scene_id), None)
    if scene is None or current is None:
        raise KeyError(f"scene {scene_id} not found")
    seconds = next((sa.duration for sa in Job(out_dir, s).read_timeline().scenes if sa.scene_id == scene_id), 5.0)
    selector = Selector(stock_clients(s), None, s.preset(script.format), out_dir, s.path(s.pipeline.cache_dir),
                        subject=video_subject([sc.visual_query for sc in script.scenes]), judge=llm_judge(s))
    used = {x for a in assets for x in (a.uid, a.url) if x}
    new = selector.swap(scene, seconds, current, used, query)

    if current.path and current.path != new.path:
        (out_dir / current.path).unlink(missing_ok=True)
    assets = [new if a.scene_id == scene_id else a for a in assets]
    write_atomic(out_dir / "assets.json", ASSETS.dump_json(assets, indent=2))
    invalidate_stage(out_dir, "render")
    return new


def actual_duration(out_dir: Path) -> float | None:
    """Real spoken length, only while timeline.json still matches the current script.json
    (after an edit the next run regenerates the voice, so the old length is meaningless)."""
    tl = out_dir / "timeline.json"
    state = _load_state(out_dir)
    if not tl.exists() or state.get("script_hash") != _hash(out_dir / "script.json"):
        return None
    return round(Timeline.model_validate_json(tl.read_text(encoding="utf-8")).duration, 1)


def run_stages(out_dir: Path, s: Settings, force: str | None = None,
               on_stage: Callable[[str, str], None] = lambda name, status: None) -> dict[str, float]:
    """Run every stage whose artifact is missing. Returns seconds spent per stage run."""
    if not (out_dir / "script.json").exists():
        raise FileNotFoundError(f"{out_dir / 'script.json'} not found")
    state = _load_state(out_dir)
    current_hash = _hash(out_dir / "script.json")
    if state.get("script_hash") and state["script_hash"] != current_hash:
        cleared = invalidate_from(out_dir, "voice")
        log.info("script.json edited → regenerating %s", ", ".join(cleared) or "nothing")
        on_stage("script", "edited → downstream reset")
    state["script_hash"] = current_hash
    if force:
        invalidate_from(out_dir, force)

    job = Job(out_dir, s, state.get("topic", ""))
    timings: dict[str, float] = {}
    for st in STAGES:
        if (out_dir / st.artifact).exists():
            on_stage(st.name, "skip")
            continue
        on_stage(st.name, "run")
        t = time.time()
        try:
            st.run(job)
        finally:
            timings[st.name] = round(time.time() - t, 1)
            state.setdefault("timings", {}).update(timings)
            _save_state(out_dir, state)
        on_stage(st.name, f"done {timings[st.name]}s")
    return timings


def resolve_output_dir(slug_or_path: str, s: Settings) -> Path:
    p = Path(slug_or_path)
    return p if p.is_absolute() or p.exists() else s.path(s.pipeline.output_dir) / slug_or_path
