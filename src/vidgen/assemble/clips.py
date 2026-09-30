"""Scenes → shots → normalized silent segments (same size/fps/codec so concat can stream-copy).

Editing choices (why the video feels cut, not assembled):
- pacing: a scene longer than SHOT_MAX is split into 2-3 shots, from the scene's extra clips or
  different parts of the same clip; clips skip their first LEAD_IN seconds (fades, pans in)
- motion: stock video keeps its own camera movement (an added zoom on top looks fake), except a tripod
  clip (focus.still), which drifts a few percent so it doesn't sit frozen; still images slowly push in,
  pull out or pan (Ken Burns). Both follow an S-curve: easing in and out reads as a camera operator, a
  constant speed as a slideshow
- transitions: straight cuts inside a scene (and out of a scene too brief to dissolve out of); the last
  TRANSITION seconds of a scene cross into the next scene's first shot, at the short pause in the voice,
  with the xfade that matches the sound there (assemble/transitions.py), rendered inside the outgoing
  segment so segments stay independent (parallel, stream-copy concat)
- framing & colour: focus.analyse() picks where to crop (subject, not centre) and nudges exposure
  toward a common level; one mild grade + vignette + fine grain gives the whole video a single,
  less "stock" look
"""

from __future__ import annotations

import math
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from vidgen import ffmpeg
from vidgen.assemble import looks
from vidgen.assemble.focus import Focus, analyse
from vidgen.config import FormatPreset
from vidgen.models import Asset, Timeline

PLACEHOLDER_COLOR = "0x1e293b"
WORKERS = 3
SEGMENT_TIMEOUT_BASE = 120  # seconds, plus 0.5s per output frame
SHOT_MAX = {"short": 3.5, "long": 6.0}  # seconds; longer scenes get several shots
MIN_SHOT = 1.4
LEAD_IN = 0.6          # skip the start of stock clips (fade-ins, camera settling)
TRANSITION = 0.18      # dissolve between scenes: short enough to read as a soft cut, not a fade
OVERSIZE = 1.12        # images are rendered larger than the output so Ken Burns never shows edges
MOTION = 0.08          # zoom amount over a shot
MOTIONS = ("push", "pan_r", "pull", "push", "pan_l")
PUNCH_IN = 0.22        # a clip reused within a scene is framed tighter, so the cut reads as a new shot
DRIFT = 0.04           # a tripod clip's camera travels this share of the frame over a shot...
DRIFT_SCALE = 1.06     # ...on a frame this much larger, so the drift never reveals an edge
# every segment leaves in one delivery format: xfade negotiates yuv444p and full-range stock clips arrive as
# yuvj (seen live: 4 formats in one video, stream-copied into a yuv444p final.mp4 phones can't play)
DELIVERY = "scale=out_range=tv,format=yuv420p"


def frame_counts(timeline: Timeline, fps: int) -> list[int]:
    """Frames per scene from cumulative boundaries, so rounding never accumulates drift."""
    counts = []
    prev = round(timeline.scenes[0].start * fps) if timeline.scenes else 0
    for s in timeline.scenes:
        # chain from the previous boundary: recomputing round(start) can disagree with
        # round(prev_end) by a frame when float sums land on .5
        end = round((s.start + s.duration) * fps)
        counts.append(end - prev)
        prev = end
    return counts


@dataclass
class Shot:
    scene_id: int
    index: int              # shot number within the scene
    kind: str               # video | image | color
    src: Path | None
    offset: float           # seconds into the source (videos)
    frames: int
    motion: str
    transition_in: bool = False  # first shot of a scene that the previous scene cross-fades into
    punch: float = 0.0           # extra zoom (PUNCH_IN when the scene already showed this source)
    focus: Focus = field(default_factory=Focus)
    analyse: bool = True         # False: keep the source's own framing/exposure (document cards)
    card: bool = False           # a document card (visuals/document.py)
    transition: str = "fade"     # the xfade into this shot when transition_in (assemble/transitions.py)
    look: str = "neutral"        # colour grade (assemble/looks.py); evidence cards always neutral


def fade_frames(p: FormatPreset) -> int:
    return round(TRANSITION * p.fps)


def plan_shots(timeline: Timeline, assets: list[Asset], p: FormatPreset, fmt: str, src_dir: Path,
               clip_seconds: dict[Path, float], look: str = "neutral") -> list[Shot]:
    """Pure planning (tested without ffmpeg): how many shots per scene, which source and which part."""
    by_scene = {a.scene_id: a for a in assets}
    tf = fade_frames(p)
    shots: list[Shot] = []
    for si, (scene, frames) in enumerate(zip(timeline.scenes, frame_counts(timeline, p.fps), strict=True)):
        asset = by_scene.get(scene.scene_id)
        sources = ([(asset.kind, src_dir / asset.path)] + [("video" if x.endswith(".mp4") else "image", src_dir / x)
                                                            for x in asset.extra]
                   if asset and asset.path else [("color", None)])
        seconds = frames / p.fps
        # a document card plays once from its start (its highlighter sweeps in at the beginning)
        card = asset is not None and asset.source == "document"
        n = 1 if sources[0][0] == "color" or card else max(1, min(math.ceil(seconds / SHOT_MAX[fmt]),
                                                          int(seconds // MIN_SHOT) or 1))
        sizes = [frames // n + (1 if k < frames % n else 0) for k in range(n)]
        uses: dict[int, int] = {}
        for k, size in enumerate(sizes):
            kind, src = sources[k % len(sources)]
            j = uses.get(k % len(sources), 0)
            uses[k % len(sources)] = j + 1
            m = sum(1 for kk in range(n) if kk % len(sources) == k % len(sources))
            offset = 0.0
            if kind == "video" and src is not None and not card:
                total = clip_seconds.get(src, 0.0)
                need = size / p.fps + TRANSITION
                lead = LEAD_IN if total > need + LEAD_IN + 0.3 else 0.0
                spare = max(0.0, total - lead - need)
                # one use: start a little in (the start is rarely the best part); several: spread out
                offset = lead + (spare * 0.3 if m == 1 else spare * j / (m - 1))
            shots.append(Shot(scene.scene_id, k, kind, src, round(offset, 3), size,
                              MOTIONS[(si + k) % len(MOTIONS)] if kind == "image" else "none",
                              transition_in=(k == 0 and si > 0),
                              punch=PUNCH_IN if j > 0 and kind != "color" else 0.0, analyse=not card,
                              card=card, look="neutral" if card else look))
    # a cross-fade needs room on both sides, and a scene this brief is left with a straight cut: dissolving
    # out of it would blur most of what it shows
    for prev, cur in zip(shots, shots[1:]):
        if cur.transition_in and (prev.frames < max(2 * tf, round(MIN_SHOT * p.fps)) or cur.frames < 2 * tf
                                  or "color" in (prev.kind, cur.kind)):
            cur.transition_in = False
    return shots


def _ease(var: str, t0: int, span: int) -> str:
    """Camera progress 0→1 across the shot's whole span (including the part already shown inside the
    previous segment's cross-fade), so motion never jumps; eased (1-cos(πx))/2: the camera starts and
    settles gently instead of moving at a constant speed. `var` is the filter's frame counter."""
    return f"((1-cos(PI*min(1,({var}+{t0})/{max(span, 1)})))/2)"


def _motion(shot: Shot, p: FormatPreset, t0: int, span: int) -> str:
    """zoompan on an oversized frame, along _ease."""
    prog = _ease("on", t0, span)
    base = 1 + shot.punch
    z = {"push": f"{base}+{MOTION}*{prog}", "pull": f"{base + MOTION}-{MOTION}*{prog}"}.get(shot.motion,
                                                                                         f"{base + MOTION}")
    x = {"pan_r": f"(iw-iw/zoom)*{prog}", "pan_l": f"(iw-iw/zoom)*(1-{prog})"}.get(shot.motion, "iw/2-(iw/zoom/2)")
    return f"zoompan=z='{z}':x='{x}':y='ih/2-(ih/zoom/2)':d=1:s={p.width}x{p.height}:fps={p.fps}"


def _stream(shot: Shot, p: FormatPreset, t0: int, span: int, frames: int) -> str:
    """Filter chain turning one input into `frames` output frames for this shot."""
    w, h = p.width, p.height
    tail = f"trim=end_frame={frames},setpts=PTS-STARTPTS,setsar=1,format=yuv420p"
    if shot.kind == "color":
        return f"scale={w}:{h},{tail}"
    grade = looks.grade(shot.look, shot.focus.brightness)
    if shot.kind == "video":
        # a punch-in is a tighter static framing: scale up, then crop the output size around the subject
        grow = (1 + shot.punch) * (DRIFT_SCALE if shot.focus.still else 1)
        ws, hs = round(w * grow / 2) * 2, round(h * grow / 2) * 2
        x, y = f"(iw-ow)*{shot.focus.x:.3f}", "(ih-oh)/2"
        if shot.focus.still:  # the crop window travels along the eased curve, direction alternating per shot
            d, prog = (1 if (shot.scene_id + shot.index) % 2 else -1), _ease("n", t0, span)
            x = f"'max(0,min(iw-ow,{x}+{d * DRIFT * w:.1f}*({prog}-0.5)))'"
            y = f"'max(0,min(ih-oh,{y}+{-d * DRIFT * h / 2:.1f}*({prog}-0.5)))'"
        return (f"fps={p.fps},scale={ws}:{hs}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h}:x={x}:y={y},{grade},{tail}")
    wo, ho = round(w * OVERSIZE / 2) * 2, round(h * OVERSIZE / 2) * 2
    return (f"scale={wo}:{ho}:force_original_aspect_ratio=increase,"
            f"crop={wo}:{ho}:x=(iw-ow)*{shot.focus.x:.3f}:y=(ih-oh)/2,"
            f"{_motion(shot, p, t0, span)},{grade},{tail}")


def _input(shot: Shot, p: FormatPreset, offset: float) -> list[str]:
    if shot.kind == "video":
        return ["-ss", f"{offset:.3f}", "-stream_loop", "-1", "-i", str(shot.src)]
    if shot.kind == "image":
        return ["-loop", "1", "-framerate", str(p.fps), "-i", str(shot.src)]
    return ["-f", "lavfi", "-i", f"color=c={PLACEHOLDER_COLOR}:s={p.width}x{p.height}:r={p.fps}"]


def shot_args(shot: Shot, nxt: Shot | None, p: FormatPreset, out: Path) -> list[str]:
    tf = fade_frames(p)
    start_t0 = tf if shot.transition_in else 0                    # continue the cross-fade's motion
    offset = shot.offset + (tf / p.fps if shot.transition_in else 0.0)
    span = shot.frames + start_t0
    inputs = _input(shot, p, offset)
    graph = f"[0:v]{_stream(shot, p, start_t0, span, shot.frames)}[a]"
    if nxt is not None and nxt.transition_in:
        inputs += _input(nxt, p, nxt.offset)
        nxt_span = nxt.frames + tf
        graph += (f";[1:v]{_stream(nxt, p, 0, nxt_span, tf)}[b]"
                  f";[a][b]xfade=transition={nxt.transition}:duration={tf / p.fps:.4f}"
                  f":offset={(shot.frames - tf) / p.fps:.3f},{DELIVERY}[v]")
    else:
        graph = graph.replace("[a]", f",{DELIVERY}[v]")
    return [*inputs, "-filter_complex", graph, "-map", "[v]", "-frames:v", str(shot.frames), "-an",
            "-r", str(p.fps), *ffmpeg.video_encoder(), str(out)]


def cut_times(shots: list[Shot], fps: int) -> list[float]:
    """Seconds at which a scene dissolves into the next (where a whoosh belongs)."""
    out, frames = [], 0
    for shot in shots:
        if shot.transition_in:
            out.append(round(frames / fps, 3))
        frames += shot.frames
    return out


def _analyse_all(shots: list[Shot], p: FormatPreset) -> None:
    aspect = p.width / p.height

    def one(shot: Shot) -> None:
        if shot.kind != "color" and shot.src is not None and shot.analyse:
            shot.focus = analyse(shot.src, shot.kind, shot.offset, shot.frames / p.fps, aspect)

    with ThreadPoolExecutor(4) as pool:
        list(pool.map(one, shots))


def render_segments(assets: list[Asset], timeline: Timeline, p: FormatPreset, out_dir: Path, fmt: str = "short",
                    cues_for: Callable[[list[float]], list] | None = None,
                    look: str = "neutral") -> tuple[list[Path], list]:
    """Segment files in order, and the sound cues. The cues are planned from the shot plan's scene-change
    times BEFORE rendering (`cues_for`), because each scene change's transition follows its cue."""
    seg_dir = out_dir / "segments"
    seg_dir.mkdir(exist_ok=True)
    for old in seg_dir.glob("seg_*.mp4"):  # shot count can differ from a previous render
        old.unlink()
    videos = {out_dir / a.path for a in assets if a.path and a.kind == "video"}
    videos |= {out_dir / x for a in assets for x in a.extra if x.endswith(".mp4")}
    with ThreadPoolExecutor(4) as pool:
        clip_seconds = dict(zip(videos, pool.map(_safe_duration, videos)))
    shots = plan_shots(timeline, assets, p, fmt, out_dir, clip_seconds, look)
    cues = cues_for(cut_times(shots, p.fps)) if cues_for else []
    from vidgen.assemble.transitions import assign_transitions   # transitions imports Shot from here
    assign_transitions(shots, cues, p.fps)
    _analyse_all(shots, p)

    jobs = [(s, shots[i + 1] if i + 1 < len(shots) else None, seg_dir / f"seg_{i:04d}.mp4")
            for i, s in enumerate(shots)]

    def render(job) -> None:
        shot, nxt, out = job
        # a corrupt clip under -stream_loop -1 can spin forever; bound each segment
        ffmpeg.run(shot_args(shot, nxt, p, out), timeout=SEGMENT_TIMEOUT_BASE + shot.frames * 0.5)

    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(render, jobs))
    return [j[2] for j in jobs], cues


def _safe_duration(path: Path) -> float:
    try:
        return ffmpeg.duration(path)
    except ffmpeg.FFmpegError:
        return 0.0
