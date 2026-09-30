"""A series: a YAML frame of episodes on a weekly rhythm (series/*.yaml), made as they fall due.

Each episode has a topic per language, a pillar (the series' recurring themes) and the brief angle to write
(explain / myth / story). Dates come from `start` and `weekdays` — episode k takes the k-th slot — unless an
episode pins its own `date`. `vidgen series FILE` makes every (episode, language) due by today and not made
yet: brief → the episode's angle → script → voice → visuals → video. It records the video folder in
<file>.state.json the moment it exists, so a run never makes one twice and an interrupted one is resumed.
Nothing is published: fact-check flags are listed for review before posting.
"""

from __future__ import annotations

import json
import logging
import time
import datetime as dt
from datetime import timedelta
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from vidgen.config import Settings
from vidgen.fsutil import write_atomic
from vidgen.models import Brief

log = logging.getLogger(__name__)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class Episode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    pillar: str
    angle: Literal["explain", "myth", "story"]
    topic: dict[str, str]          # language → topic
    date: dt.date | None = None    # pinned date; otherwise the next weekly slot


class Series(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    start: dt.date
    weekdays: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]]
    format: Literal["short", "long"] = "short"
    langs: list[Literal["vi", "en"]]
    look: str | None = None        # pin one colour look for the whole series (assemble/looks.py); None = per video
    pillars: dict[str, str] = {}   # pillar → what it covers (documentation for whoever extends the frame)
    episodes: list[Episode]

    @model_validator(mode="after")
    def _complete(self) -> Series:
        ids = [e.id for e in self.episodes]
        if len(ids) != len(set(ids)):
            raise ValueError("episode ids must be unique")
        for e in self.episodes:
            missing = [lang for lang in self.langs if not e.topic.get(lang, "").strip()]
            if missing:
                raise ValueError(f"episode {e.id}: no topic for {', '.join(missing)}")
        if not self.weekdays:
            raise ValueError("weekdays must not be empty")
        from vidgen.assemble.looks import LOOKS
        if self.look is not None and self.look not in LOOKS:
            raise ValueError(f"look must be one of {', '.join(LOOKS)}")
        return self


def load(path: Path) -> Series:
    return Series.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def schedule(s: Series) -> list[tuple[dt.date, Episode]]:
    """(date, episode) in frame order; unpinned episodes take the weekly slots from `start` in turn."""
    days = {WEEKDAYS.index(d) for d in s.weekdays}
    slot = s.start - timedelta(days=1)
    out = []
    for e in s.episodes:
        if e.date is not None:
            out.append((e.date, e))
            continue
        slot += timedelta(days=1)
        while slot.weekday() not in days:
            slot += timedelta(days=1)
        out.append((slot, e))
    return out


def key(e: Episode, lang: str) -> str:
    return f"{e.id}/{lang}"


@dataclass
class Item:
    date: dt.date
    episode: Episode
    lang: str
    status: str             # made | unfinished | due | planned
    out_dir: Path | None    # the episode's video folder, if one exists


def items(s: Series, state: dict[str, str], today: dt.date, locate) -> list[Item]:
    """Every (episode, language) with its status — the one rule the CLI table and run() share. A recorded
    folder that no longer exists (deleted by hand) counts as not made: it is made again, never "resumed"."""
    out = []
    for d, e in schedule(s):
        for lang in s.langs:
            slug = state.get(key(e, lang))
            folder = locate(slug) if slug else None
            if folder is not None and not folder.exists():
                folder = None
            status = ("made" if folder is not None and (folder / "final.mp4").exists() else
                      "unfinished" if folder is not None else "due" if d <= today else "planned")
            out.append(Item(d, e, lang, status, folder))
    return out


def angle_index(brief: Brief, style: str) -> int:
    """The brief angle with this style (the first angle if the model didn't write one)."""
    return next((i for i, a in enumerate(brief.angles) if a.style == style), 0)


def state_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.state.json")


def _read_state(path: Path) -> dict[str, str]:
    p = state_path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


# --- the pipeline steps (patched out in tests) ---------------------------------------------------------------
def _dir(slug: str, settings: Settings) -> Path:
    from vidgen.pipeline import resolve_output_dir

    return resolve_output_dir(slug, settings)


def _create(e: Episode, lang: str, s: Series, fmt: str, settings: Settings) -> Path:
    from vidgen import pipeline

    out_dir = pipeline.init_video(e.topic[lang], fmt, lang, settings)
    state = pipeline.load_state(out_dir)
    state["series"] = {"name": s.name, "episode": e.id, "angle": e.angle}   # _finish writes this angle
    if s.look:
        state["look"] = s.look   # render uses it instead of the writer's pick: one look for the channel
    pipeline.save_state(out_dir, state)
    return out_dir


def _finish(out_dir: Path, settings: Settings) -> dict:
    """Brief (if missing) → the recorded angle → script (if missing) → every remaining stage.

    The folder must not be worked on twice at once (seen live: this run and a studio render on one folder —
    one deleted the other's segments). The whole run holds the folder lock (folderlock.py): a studio job or
    another run is refused, and the OS frees it however this process ends. job.json shows the progress."""
    from vidgen import pipeline
    from vidgen.folderlock import FolderLock
    from vidgen.web.jobs import JobStatus

    with FolderLock(out_dir, "series"):   # raises FolderBusyError if another owner works on it
        job = JobStatus(out_dir.name, "series", status="running", out_dir=out_dir)
        job.save()

        def on_stage(name: str, status: str) -> None:
            job.stages[name], job.current = status, name if status == "run" else ""
            job.save()
        try:
            if pipeline.load_brief(out_dir) is None:
                on_stage("brief", "run")
                pipeline.write_brief(out_dir, settings)
                on_stage("brief", "done")
            if not (out_dir / "script.json").exists():
                brief = pipeline.load_brief(out_dir)
                style = pipeline.load_state(out_dir).get("series", {}).get("angle", "explain")
                pipeline.choose_angle(out_dir, angle_index(brief, style))
                on_stage("script", "run")
                pipeline.write_script(out_dir, settings)
                on_stage("script", "done")
            pipeline.run_stages(out_dir, settings, on_stage=on_stage)
            job.status = "done"
        except BaseException as e:   # Ctrl-C too: the status must never stay "running"
            job.status = "interrupted" if isinstance(e, KeyboardInterrupt) else "error"
            job.error = str(e) or type(e).__name__
            raise
        finally:
            job.current, job.finished_at = "", time.time()
            job.save()
    fc = out_dir / "factcheck.json"
    return {"flags": len(json.loads(fc.read_text(encoding="utf-8")).get("issues", [])) if fc.exists() else None}


def run(path: Path, settings: Settings | None, today: dt.date | None = None, limit: int | None = None,
        on_video=lambda d, e, lang, out_dir, result: None) -> list[Path]:
    """Make (or resume) every video due by `today`. Returns the folders worked on."""
    s, today = load(path), today or dt.date.today()
    state = _read_state(path)
    worked: list[Path] = []
    for item in items(s, state, today, lambda slug: _dir(slug, settings)):
        if item.status not in ("due", "unfinished"):
            continue
        if limit is not None and len(worked) >= limit:
            break
        out_dir = item.out_dir
        if item.status == "due":
            out_dir = _create(item.episode, item.lang, s, s.format, settings)
            state[key(item.episode, item.lang)] = out_dir.name
            write_atomic(state_path(path), json.dumps(state, ensure_ascii=False, indent=2))
        try:
            result = _finish(out_dir, settings)
        except Exception as e:  # one failed video must not stop the rest; it resumes next run
            log.error("series %s episode %s/%s failed: %s", s.name, item.episode.id, item.lang, e)
            result = {"error": str(e)}
        worked.append(out_dir)
        on_video(item.date, item.episode, item.lang, out_dir, result)
    return worked
