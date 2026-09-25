"""Segments + voice + music + burned captions → final.mp4."""

import logging
import shutil
from pathlib import Path

from vidgen import ffmpeg
from vidgen.assemble.clips import render_segments
from vidgen.assemble.music import pick_music
from vidgen.assemble.subtitles import build_ass
from vidgen.config import ROOT, FormatPreset
from vidgen.models import Asset, Script, Timeline

log = logging.getLogger(__name__)
FONTS_DIR = ROOT / "assets" / "fonts"
MUSIC_DIR = ROOT / "assets" / "music"
MUSIC_VOLUME = 0.35       # before ducking; sidechain pushes it further down under speech
LOUDNESS = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"  # -14 LUFS: YouTube/TikTok target
STEREO = "aresample=48000,aformat=channel_layouts=stereo"


def audio_filter(has_music: bool) -> str:
    if not has_music:
        return f"[1:a]{STEREO},{LOUDNESS}[a]"
    return (
        f"[1:a]{STEREO},asplit=2[vo][sc];"
        f"[2:a]{STEREO},volume={MUSIC_VOLUME}[mu];"
        "[mu][sc]sidechaincompress=threshold=0.03:ratio=12:attack=15:release=350[duck];"
        f"[vo][duck]amix=inputs=2:duration=first:normalize=0,{LOUDNESS}[a]"
    )


def final_args(duration: float, music: Path | None) -> list[str]:
    """Paths are relative to the output dir (run with cwd there) to avoid Windows ':' escaping in filters."""
    inputs = ["-f", "concat", "-safe", "0", "-i", "segments/list.txt", "-i", "voice.wav"]
    if music:
        inputs += ["-stream_loop", "-1", "-i", str(music)]
    graph = f"[0:v]ass=subs.ass:fontsdir=fonts[v];{audio_filter(music is not None)}"
    return [*inputs, "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
            *ffmpeg.video_encoder(), "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.3f}", "-movflags", "+faststart", "final.mp4"]


def render_video(script: Script, timeline: Timeline, assets: list[Asset], preset: FormatPreset,
                 out_dir: Path, seed: str) -> Path:
    segments = render_segments(assets, timeline, preset, out_dir)
    (out_dir / "segments" / "list.txt").write_text(
        "".join(f"file '{p.name}'\n" for p in segments), encoding="utf-8")
    (out_dir / "subs.ass").write_text(build_ass(script, timeline, preset), encoding="utf-8")
    shutil.copytree(FONTS_DIR, out_dir / "fonts", dirs_exist_ok=True)

    music = pick_music(MUSIC_DIR, seed)
    if music is None:
        log.warning("no tracks in %s — rendering without background music", MUSIC_DIR)
    ffmpeg.run(final_args(timeline.duration, music), cwd=out_dir)
    return out_dir / "final.mp4"
