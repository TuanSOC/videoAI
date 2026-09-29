"""Segments + voice + music + burned captions → final.mp4."""

import json
import logging
import math
import shutil
from pathlib import Path

from vidgen import ffmpeg
from vidgen.assemble.clips import render_segments
from vidgen.assemble import sfx as sound
from vidgen.assemble.music import music_start, pick_music, track_credit
from vidgen.assemble.subtitles import build_ass
from vidgen.config import ROOT, FormatPreset
from vidgen.fsutil import replace_with_retry, write_atomic
from vidgen.models import Asset, Script, Timeline

log = logging.getLogger(__name__)
FONTS_DIR = ROOT / "assets" / "fonts"
MUSIC_DIR = ROOT / "assets" / "music"
MUSIC_FILE = "music.json"  # track used by the render + its credit line, for the video description
SFX_DIR = ROOT / "assets" / "sfx"
SFX_FILE = "sfx.json"      # the cues the render placed, for inspection
PART_FILE = "final.part.mp4"  # the encode in progress
MUSIC_UNDER_VOICE_DB = 18  # music sits this far below the voice's mean level...
FALLBACK_MUSIC_DB = -9.1   # ...or at the old fixed 0.35 gain when a level can't be measured
MUSIC_GAIN_RANGE = (-40.0, 6.0)
SILENT_DB = -60.0
LOUDNESS = "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000"  # -14 LUFS: YouTube/TikTok target
STEREO = "aresample=48000,aformat=channel_layouts=stereo"
FADE_IN, FADE_OUT = 1.5, 2   # seconds of music fade at the start and the end


LIMIT = "alimiter=limit=0.89:attack=5:release=50:level=disabled"  # SFX transients never clip


def music_gain_db(voice_mean: float | None, music_mean: float | None) -> float:
    """Gain putting the track MUSIC_UNDER_VOICE_DB below the voice (both as measured means). A silent
    or near-silent track is "unmeasurable" (it would get +50 dB of noise), and the gain is clamped."""
    if voice_mean is None or music_mean is None or not all(map(math.isfinite, (voice_mean, music_mean))) \
            or music_mean < SILENT_DB:
        return FALLBACK_MUSIC_DB
    return round(min(max(voice_mean - MUSIC_UNDER_VOICE_DB - music_mean, *MUSIC_GAIN_RANGE[:1]),
                     MUSIC_GAIN_RANGE[1]), 1)


def audio_filter(has_music: bool, duration: float = 0.0, music_db: float = FALLBACK_MUSIC_DB,
                 sfx_input: int | None = None) -> str:
    """Voice (+ SFX) → limiter → (+ music ducked under the voice) → loudness normalisation.
    Input 1 is the voice, 2 the music if any, then the SFX track if any."""
    voice = f"[1:a]{STEREO}"
    if sfx_input is None and not has_music:
        return f"{voice},{LOUDNESS}[a]"
    parts = [f"{voice},asplit=2[vo][sc]" if has_music else f"{voice}[vo]"]
    bus = "[vo]"
    if sfx_input is not None:
        parts.append(f"[{sfx_input}:a]{STEREO}[fx];[vo][fx]amix=inputs=2:duration=first:normalize=0[bus]")
        bus = "[bus]"
    if has_music:
        fades = f"afade=t=in:d={FADE_IN}" + (f",afade=t=out:st={duration - FADE_OUT:.2f}:d={FADE_OUT}"
                                              if duration > FADE_IN + FADE_OUT else "")
        parts.append(f"[2:a]{STEREO},volume={music_db}dB,{fades}[mu]")
        # already 18 dB down: a gentle extra dip under speech, not the old hard pumping
        parts.append("[mu][sc]sidechaincompress=threshold=0.05:ratio=3:attack=20:release=300[duck]")
        parts.append(f"{bus}[duck]amix=inputs=2:duration=first:normalize=0,{LIMIT},{LOUDNESS}[a]")
    else:
        parts.append(f"{bus}{LIMIT},{LOUDNESS}[a]")
    return ";".join(parts)


def final_args(duration: float, music: Path | None, music_start: float = 0.0,
               music_gain_db: float = FALLBACK_MUSIC_DB, sfx: Path | None = None,
               out: str = "final.mp4") -> list[str]:
    """Paths are relative to the output dir (run with cwd there) to avoid Windows ':' escaping in filters."""
    inputs = ["-f", "concat", "-safe", "0", "-i", "segments/list.txt", "-i", "voice.wav"]
    if music:
        inputs += ["-ss", f"{music_start:.2f}", "-stream_loop", "-1", "-i", str(music)]
    sfx_input = None
    if sfx:
        sfx_input = 3 if music else 2
        inputs += ["-i", str(sfx)]
    # two independent graphs: with video and audio in one graph FFmpeg 8.1's loudnorm can emit NaN
    # samples for some voices ("Input contains NaN" from the AAC encoder, render fails)
    return [*inputs, "-filter_complex", "[0:v]ass=subs.ass:fontsdir=fonts[v]",
            "-filter_complex", audio_filter(music is not None, duration, music_gain_db, sfx_input),
            "-map", "[v]", "-map", "[a]",
            *ffmpeg.video_encoder(), "-c:a", "aac", "-b:a", "192k",
            "-t", f"{duration:.3f}", "-movflags", "+faststart", out]


def render_video(script: Script, timeline: Timeline, assets: list[Asset], preset: FormatPreset,
                 out_dir: Path, seed: str, sfx_density: str | None = "subtle") -> Path:
    """sfx_density: a sfx.DENSITY preset, or None for no sound effects."""
    # sound first: each scene change's transition follows the cue planned there (assemble/transitions.py)
    segments, cues = render_segments(
        assets, timeline, preset, out_dir, script.format,
        cues_for=lambda cuts: sound.detect_cues(script, timeline, cuts, sfx_density, seed) if sfx_density else [])
    (out_dir / "segments" / "list.txt").write_text(
        "".join(f"file '{p.name}'\n" for p in segments), encoding="utf-8")
    (out_dir / "subs.ass").write_text(build_ass(script, timeline, preset), encoding="utf-8")
    shutil.copytree(FONTS_DIR, out_dir / "fonts", dirs_exist_ok=True)

    music = pick_music(MUSIC_DIR, seed, script.mood)
    start, gain = 0.0, FALLBACK_MUSIC_DB
    if music is None:
        log.warning("no tracks in %s — rendering without background music", MUSIC_DIR)
    else:
        start = music_start(ffmpeg.duration(music), timeline.duration, seed)
        gain = music_gain_db(ffmpeg.mean_volume(out_dir / "voice.wav"), ffmpeg.mean_volume(music))
        log.info("music: %s (mood %s) from %.1fs at %.1f dB", music.name, script.mood or "any", start, gain)

    sfx_track = None
    (out_dir / "sfx.wav").unlink(missing_ok=True)
    if cues:
        sfx_track = sound.build_sfx_track(cues, sound.ensure_library(SFX_DIR), timeline.duration,
                                          out_dir / "sfx.wav")
    write_atomic(out_dir / SFX_FILE, json.dumps([c.__dict__ for c in cues], indent=1))
    # final.mp4 existing means "render done": encode beside it and move it in only when complete
    part = out_dir / PART_FILE
    try:
        ffmpeg.run(final_args(timeline.duration, music, start, gain,
                              Path(sfx_track.name) if sfx_track else None, out=PART_FILE), cwd=out_dir)
        write_atomic(out_dir / MUSIC_FILE, json.dumps(
            {"track": music.name if music else "", "credit": track_credit(music) if music else ""},
            ensure_ascii=False))
        replace_with_retry(part, out_dir / "final.mp4")
    finally:
        part.unlink(missing_ok=True)
    return out_dir / "final.mp4"
