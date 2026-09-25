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
from vidgen.models import Asset, Script, Timeline

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
    from vidgen.script.writer import generate

    s = job.settings
    script = generate(job.topic, fmt, lang, s.preset(fmt), default_chain(s))
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
    render_video(script, job.read_timeline(), job.read_assets(), job.settings.preset(script.format),
                 job.out_dir, seed=job.out_dir.name)


def _metadata(job: Job) -> None:
    from vidgen.metadata import generate_metadata, save_metadata
    from vidgen.script.llm import default_chain

    meta = generate_metadata(job.read_script(), job.read_assets(), default_chain(job.settings))
    save_metadata(meta, job.out_dir / "metadata.json")


# script.json is created by create_script() and owned by the user after review; it is never
# regenerated or deleted by the runner.
STAGES = [
    Stage("voice", "timeline.json", ("voice", "voice.wav"), _voice),
    Stage("visuals", "assets.json", ("visuals",), _visuals),
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
    """(Re)generate script.json from the topic/format/lang recorded by init_video."""
    state = _load_state(out_dir)
    t = time.time()
    _script(Job(out_dir, s, state["topic"]), state["format"], state["lang"])
    state["script_hash"] = _hash(out_dir / "script.json")
    state.setdefault("timings", {})["script"] = round(time.time() - t, 1)
    _save_state(out_dir, state)


def create_script(topic: str, fmt: str, lang: str, s: Settings) -> Path:
    out_dir = init_video(topic, fmt, lang, s)
    write_script(out_dir, s)
    return out_dir


def load_state(out_dir: Path) -> dict:
    return _load_state(out_dir)


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
