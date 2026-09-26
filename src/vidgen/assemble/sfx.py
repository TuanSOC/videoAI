"""Automatic, restrained sound design.

Three cues, placed from timings we already have:
- whoosh: just before a scene dissolve (the cut times come from the shot plan)
- impact: the opening hook, and shock words ("bí mật", "biến mất", "deadly"…)
- pop: numbers, years, percentages

Restraint is the point: a whoosh on every cut and a boom on every sentence is what makes a video
sound like a cheap template. Each density preset caps and spaces the cues, and cues that would land
within MIN_GAP of a stronger one are dropped.

Sounds are synthesised with FFmpeg the first time they are needed (no licence questions); any audio
file the user drops into assets/sfx/<kind folder>/ is used instead.
"""

import logging
import random
import subprocess
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from vidgen import ffmpeg
from vidgen.assemble.clips import TRANSITION
from vidgen.assemble.music import AUDIO_EXTS
from vidgen.assemble.subtitles import display_words
from vidgen.models import Script, Timeline, WordTiming
from vidgen.text import bare, ends_with, is_number

FOLDERS = {"whoosh": "whooshes", "impact": "impacts", "pop": "pops"}
PRIORITY = ("impact", "whoosh", "pop")      # who wins when two cues collide
log = logging.getLogger(__name__)
VARIANTS = 3
SYNTH = "synth2_"      # generated files; older synth_* ones (unnormalised, far too quiet) are replaced
MIN_GAP = 0.6          # seconds between any two cues
SAMPLE_RATE = 48000
SOURCE_PEAK_DB = -3.0  # every library sound is normalised to this peak...
# ...and placed at these levels: SFX peaks ~10 dB under the voice's (~-3 dBFS) peaks — heard, never on top
GAIN_DB = {"whoosh": -10.0, "impact": -8.0, "pop": -10.0}
ENVELOPE_RATE = 1000   # envelope samples per second, for finding where a sound peaks

# words that turn a sentence (bare, lower-case; multi-word entries match consecutive words)
SHOCK = {
    "vi": ["bí mật", "biến mất", "nguy hiểm", "chấn động", "cảnh báo", "sự thật", "không ai", "bất ngờ",
           "kinh hoàng", "tử vong", "thảm hoạ", "thảm họa", "đáng sợ", "bí ẩn", "sụp đổ", "mất tích",
           "thiệt mạng", "khủng khiếp", "lừa đảo", "đánh cắp"],
    "en": ["secret", "vanished", "disappeared", "deadly", "dangerous", "warning", "shocking", "nobody",
           "never", "truth", "mystery", "collapsed", "killed", "died", "terrifying", "stolen", "hacked"],
}


@dataclass(frozen=True)
class Cue:
    kind: str
    time: float        # seconds from the start of the video at which the sound PEAKS
    variant: int = 0   # which sound of the kind (taken modulo the library size)


@dataclass(frozen=True)
class Density:
    whoosh_cap: int | None
    whoosh_spacing: float
    impact_cap: int | None
    impact_spacing: float
    pop_cap: int | None
    pop_spacing: float
    shock_words: bool = True


DENSITY = {
    "minimal": Density(2, 4.0, 1, 8.0, 0, 3.0, shock_words=False),
    # a 13-scene short dissolves at every scene: 4 s spacing still gave 9 whooshes in 51 s
    "subtle": Density(None, 10.0, 2, 8.0, 4, 3.0),
    "dense": Density(None, 1.0, None, 1.0, None, 1.0),
}


def _sentences(words: list[WordTiming]) -> list[list[WordTiming]]:
    out, current = [], []
    for w in words:
        current.append(w)
        if ends_with(w.word):
            out.append(current)
            current = []
    return out + ([current] if current else [])


def _shock_hit(sentence: list[WordTiming], phrases: list[list[str]]) -> WordTiming | None:
    keys = [bare(w.word) for w in sentence]
    for i in range(len(keys)):
        for phrase in phrases:
            if keys[i:i + len(phrase)] == phrase:
                return sentence[i]
    return None


def _keep(times: list[float], cap: int | None, spacing: float) -> list[float]:
    kept: list[float] = []
    for t in times:
        if (cap is None or len(kept) < cap) and (not kept or t - kept[-1] >= spacing):
            kept.append(t)
    return kept


def detect_cues(script: Script, timeline: Timeline, cut_times: list[float], density: str = "subtle",
                seed: str = "") -> list[Cue]:
    rules = DENSITY[density]
    words = display_words(script, timeline)
    candidates: dict[str, list[float]] = {"whoosh": [], "impact": [], "pop": []}

    # peak in the middle of the dissolve (the picture changes over the TRANSITION before the cut)
    candidates["whoosh"] = _keep([round(max(0.0, c - TRANSITION / 2), 3) for c in sorted(cut_times)],
                                 rules.whoosh_cap, rules.whoosh_spacing)

    impacts = [0.0] if words else []  # the hook: the very first moment
    if rules.shock_words:
        phrases = [p.split() for p in SHOCK.get(script.lang, [])]
        for sentence in _sentences(words):
            hit = _shock_hit(sentence, phrases)
            if hit is not None and hit.start > 0:
                impacts.append(round(hit.start, 3))
    candidates["impact"] = _keep(impacts, rules.impact_cap, rules.impact_spacing)

    candidates["pop"] = _keep([round(w.start, 3) for w in words if is_number(w.word)],
                              rules.pop_cap, rules.pop_spacing)

    # stronger kinds claim their moment first; a weaker cue too close to an accepted one is dropped
    accepted: list[Cue] = []
    for kind in PRIORITY:
        for t in candidates[kind]:
            if all(abs(t - c.time) >= MIN_GAP for c in accepted):
                variant = random.Random(f"{seed}/{kind}/{t:.3f}").randrange(1000)
                accepted.append(Cue(kind, t, variant))
    return sorted(accepted, key=lambda c: c.time)


# --- sounds ------------------------------------------------------------------------------------------
def _synth_args(kind: str, k: int, out: Path) -> list[str]:
    """FFmpeg recipes; k (0..VARIANTS-1) varies pitch/length so repeats don't sound identical."""
    stereo = f"aresample={SAMPLE_RATE},aformat=channel_layouts=stereo"
    if kind == "whoosh":
        d = (0.55, 0.7, 0.85)[k]
        cutoff = (3500, 5000, 6500)[k]
        graph = (f"anoisesrc=d={d}:c=pink:a=0.9:r={SAMPLE_RATE},highpass=f=250,lowpass=f={cutoff},"
                 f"afade=t=in:d={d * 0.65:.3f}:curve=exp,afade=t=out:st={d * 0.65:.3f}:d={d * 0.35:.3f},"
                 f"volume=6dB,{stereo}")
        return ["-f", "lavfi", "-i", graph, "-c:a", "pcm_s16le", str(out)]
    if kind == "impact":
        f = (48, 56, 64)[k]
        graph = (f"sine=f={f}:d=1.4:r={SAMPLE_RATE},afade=t=out:st=0:d=1.4:curve=exp,volume=4dB[b];"
                 f"anoisesrc=d=0.12:c=brown:a=0.9:r={SAMPLE_RATE},lowpass=f=900,afade=t=out:st=0:d=0.12[n];"
                 f"[b][n]amix=inputs=2:duration=longest:normalize=0,{stereo}")
        return ["-filter_complex", graph, "-c:a", "pcm_s16le", str(out)]
    f = (680, 880, 1150)[k]
    graph = (f"sine=f={f}:d=0.09:r={SAMPLE_RATE},afade=t=in:d=0.004,afade=t=out:st=0.012:d=0.078:curve=exp,"
             f"volume=-2dB,{stereo}")
    return ["-f", "lavfi", "-i", graph, "-c:a", "pcm_s16le", str(out)]


def _normalise(path: Path) -> None:
    peak = ffmpeg.volume_stats(path)[1]
    if peak is not None and abs(peak - SOURCE_PEAK_DB) > 0.3:
        tmp = path.with_name(f".{path.stem}.norm.wav")
        ffmpeg.run(["-i", str(path), "-af", f"volume={SOURCE_PEAK_DB - peak:.2f}dB", "-c:a", "pcm_s16le", str(tmp)])
        tmp.replace(path)


def _usable(path: Path) -> bool:
    try:
        return ffmpeg.duration(path) > 0
    except ffmpeg.FFmpegError:
        return False


def ensure_library(root: Path) -> dict[str, list[Path]]:
    """Sounds per kind: the user's (playable) files if the folder has any, else synthesised ones
    (made once, normalised to SOURCE_PEAK_DB)."""
    library: dict[str, list[Path]] = {}
    for kind, folder in FOLDERS.items():
        d = root / folder
        d.mkdir(parents=True, exist_ok=True)
        files = sorted(p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXTS)
        for stale in (p for p in files if p.name.startswith("synth") and not p.name.startswith(SYNTH)):
            stale.unlink(missing_ok=True)
        mine = [p for p in files if not p.name.startswith("synth")]
        usable = [p for p in mine if _usable(p)]
        for bad in set(mine) - set(usable):
            log.warning("sfx: skipping unreadable file %s", bad)
        if usable:
            library[kind] = usable
            continue
        synth = [d / f"{SYNTH}{kind}_{k + 1}.wav" for k in range(VARIANTS)]
        for k, path in enumerate(synth):
            if not path.exists():
                ffmpeg.run(_synth_args(kind, k, path))
                _normalise(path)
        library[kind] = synth
    return library


def envelope(path: Path) -> np.ndarray:
    """Peak level per 1/ENVELOPE_RATE s (mono), for finding where a sound hits hardest."""
    rate = ENVELOPE_RATE * 8
    raw = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-ac", "1",
                          "-ar", str(rate), "-f", "s16le", "-"], capture_output=True).stdout
    samples = np.abs(np.frombuffer(raw, dtype=np.int16).astype(np.int32))
    n = len(samples) // 8
    return samples[:n * 8].reshape(n, 8).max(axis=1) if n else np.zeros(1)


@lru_cache(maxsize=64)
def _peak_offset(path: str, mtime: float) -> float:
    return float(np.argmax(envelope(Path(path)))) / ENVELOPE_RATE


def peak_offset(path: Path) -> float:
    """Seconds from the start of a sound to its loudest moment (whooshes swell; impacts hit at once)."""
    return _peak_offset(str(path), path.stat().st_mtime)


def build_sfx_track(cues: list[Cue], library: dict[str, list[Path]], duration: float, out: Path) -> Path | None:
    """All cues on one stereo track exactly `duration` long, each placed so its PEAK lands on cue.time,
    or None when there is nothing to play. Each sound file is an input once (asplit per use) and the
    graph goes through a file: hundreds of cues would overflow the Windows command line otherwise."""
    cues = [c for c in cues if library.get(c.kind) and c.time < duration]
    if not cues:
        return None
    files: list[Path] = []
    uses: dict[int, list[int]] = {}
    placed: list[tuple[int, Cue]] = []
    for c in cues:
        sounds = library[c.kind]
        path = sounds[c.variant % len(sounds)]
        if path not in files:
            files.append(path)
        k = files.index(path)
        uses.setdefault(k, []).append(len(placed))
        placed.append((k, c))
    chains = []
    for k, idx in uses.items():
        labels = "".join(f"[u{i}]" for i in idx)
        chains.append(f"[{k}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,asplit={len(idx)}{labels}")
    for i, (k, c) in enumerate(placed):
        start = c.time - peak_offset(files[k])
        trim = f"atrim=start={-start:.3f},asetpts=PTS-STARTPTS," if start < 0 else ""
        ms = max(0, round(start * 1000))
        chains.append(f"[u{i}]{trim}volume={GAIN_DB[c.kind]}dB,adelay={ms}|{ms}[s{i}]")
    # a silent bed exactly `duration` long sets the length (apad after a big amix stopped short)
    mix = f"[{len(files)}:a]" + "".join(f"[s{i}]" for i in range(len(placed)))
    chains.append(f"{mix}amix=inputs={len(placed) + 1}:duration=first:normalize=0,atrim=end={duration:.3f}[out]")
    with tempfile.TemporaryDirectory() as tmp:
        graph = Path(tmp) / "sfx_graph.txt"
        graph.write_text(";\n".join(chains), encoding="utf-8")
        inputs = [a for f in files for a in ("-i", str(f))]
        inputs += ["-f", "lavfi", "-t", f"{duration:.3f}", "-i", f"anullsrc=r={SAMPLE_RATE}:cl=stereo"]
        ffmpeg.run([*inputs, "-/filter_complex", str(graph), "-map", "[out]", "-c:a", "pcm_s16le", str(out)])
    return out
