"""Segments + voice + music + burned captions → final.mp4."""

import logging
import shutil
from pathlib import Path

from vidgen import ffmpeg
from vidgen.assemble.clips import render_segments
from vidgen.assemble.music import music_start, pick_music
from vidgen.assemble.subtitles import build_ass
from vidgen.config import ROOT, FormatPreset
from vidgen.models import Asset, Script, Timeline

log = logging.getLogger(__name__)
FONTS_DIR = ROOT / "assets" / "fonts"
MUSIC_DIR = ROOT / "assets" / "music"
MUSIC_VOLUME = 0.35       # before ducking; sidechain pushes it further down under speech
LOUDNESS = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"  # -14 LUFS: YouTube/TikTok target
STEREO = "aresample=48000,aformat=channel_layouts=stereo"
FADE_IN, FADE_OUT = 1.5, 2   # seconds of music fade at the start and the end


def audio_filter(has_music: bool, duration: float = 0.0) -> str:
    if not has_music:
        return f"[1:a]{STEREO},{LOUDNESS}[a]"
    fades = f"afade=t=in:d={FADE_IN}" + (f",afade=t=out:st={duration - FADE_OUT:.2f}:d={FADE_OUT}"
                                          if duration > FADE_IN + FADE_OUT else "")
    return (
        f"[1:a]{STEREO},asplit=2[vo][sc];"
        f"[2:a]{STEREO},volume={MUSIC_VOLUME},{fades}[mu];"
        "[mu][sc]sidechaincompress=threshold=0.03:ratio=12:attack=15:release=350[duck];"
        f"[vo][duck]amix=inputs=2:duration=first:normalize=0,{LOUDNESS}[a]"
    )


def final_args(duration: float, music: Path | None, music_start: float = 0.0) -> list[str]:
    """Paths are relative to the output dir (run with cwd there) to avoid Windows ':' escaping in filters."""
    inputs = ["-f", "concat", "-safe", "0", "-i", "segments/list.txt", "-i", "voice.wav"]
    if music:
        inputs += ["-ss", f"{music_start:.2f}", "-stream_loop", "-1", "-i", str(music)]
    # two independent graphs: with video and audio in one graph FFmpeg 8.1's loudnorm can emit NaN
    # samples for some voices ("Input contains NaN" from the AAC encoder, render fails)
    return [*inputs, "-filter_complex", "[0:v]ass=subs.ass:fontsdir=fonts[v]",
            "-filter_complex", audio_filter(music is not None, duration), "-map", "[v]", "-map", "[a]",
            *ffmpeg.video_encoder(), "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.3f}", "-movflags", "+faststart", "final.mp4"]


def render_video(script: Script, timeline: Timeline, assets: list[Asset], preset: FormatPreset,
                 out_dir: Path, seed: str) -> Path:
    segments = render_segments(assets, timeline, preset, out_dir, script.format)
    (out_dir / "segments" / "list.txt").write_text(
        "".join(f"file '{p.name}'\n" for p in segments), encoding="utf-8")
    (out_dir / "subs.ass").write_text(build_ass(script, timeline, preset), encoding="utf-8")
    shutil.copytree(FONTS_DIR, out_dir / "fonts", dirs_exist_ok=True)

    music = pick_music(MUSIC_DIR, seed, script.mood)
    start = 0.0
    if music is None:
        log.warning("no tracks in %s — rendering without background music", MUSIC_DIR)
    else:
        start = music_start(ffmpeg.duration(music), timeline.duration, seed)
        log.info("music: %s (mood %s) from %.1fs", music.name, script.mood or "any", start)
    ffmpeg.run(final_args(timeline.duration, music, start), cwd=out_dir)
    return out_dir / "final.mp4"
