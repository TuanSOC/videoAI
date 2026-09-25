"""Per-scene visual → normalized silent segment (same size/fps/codec so concat can stream-copy)."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from vidgen import ffmpeg
from vidgen.config import FormatPreset
from vidgen.models import Asset, Timeline

PLACEHOLDER_COLOR = "0x1e293b"
KEN_BURNS_MAX_ZOOM = 1.15
WORKERS = 3  # consumer NVENC allows several sessions; more workers just contend
SEGMENT_TIMEOUT_BASE = 120  # seconds, plus 0.5s per output frame


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


def segment_args(asset: Asset, src_dir: Path, frames: int, p: FormatPreset, out: Path) -> list[str]:
    w, h, fps = p.width, p.height, p.fps
    fill = f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
    tail = "setsar=1,format=yuv420p"
    if asset.kind == "video":
        inputs = ["-stream_loop", "-1", "-i", str(src_dir / asset.path)]
        vf = f"{fill},fps={fps},{tail}"
    elif asset.kind == "image":
        # upscale 2x before zoompan to avoid the integer-pixel jitter zoompan is known for
        step = (KEN_BURNS_MAX_ZOOM - 1) / max(frames, 1)
        inputs = ["-i", str(src_dir / asset.path)]
        vf = (f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,crop={w * 2}:{h * 2},"
              f"zoompan=z='min(zoom+{step:.6f},{KEN_BURNS_MAX_ZOOM})':d={frames}"
              f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={w}x{h}:fps={fps},{tail}")
    else:
        inputs = ["-f", "lavfi", "-i", f"color=c={PLACEHOLDER_COLOR}:s={w}x{h}:r={fps}"]
        vf = tail
    return [*inputs, "-vf", vf, "-frames:v", str(frames), "-an", "-r", str(fps),
            *ffmpeg.video_encoder(), str(out)]


def render_segments(assets: list[Asset], timeline: Timeline, p: FormatPreset, out_dir: Path) -> list[Path]:
    seg_dir = out_dir / "segments"
    seg_dir.mkdir(exist_ok=True)
    by_scene = {a.scene_id: a for a in assets}
    jobs = []
    for scene, frames in zip(timeline.scenes, frame_counts(timeline, p.fps), strict=True):
        asset = by_scene.get(scene.scene_id) or Asset(scene_id=scene.scene_id, path="", kind="color", source="placeholder")
        jobs.append((asset, frames, seg_dir / f"seg_{scene.scene_id:03d}.mp4"))

    def render(job) -> None:
        asset, frames, out = job
        # a corrupt clip under -stream_loop -1 can spin forever; bound each segment
        ffmpeg.run(segment_args(asset, out_dir, frames, p, out), timeout=SEGMENT_TIMEOUT_BASE + frames * 0.5)

    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(render, jobs))
    return [j[2] for j in jobs]
