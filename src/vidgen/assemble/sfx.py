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

import random
from dataclasses import dataclass
from pathlib import Path

from vidgen import ffmpeg
from vidgen.assemble.subtitles import _bare, _is_number, display_words
from vidgen.models import Script, Timeline, WordTiming

FOLDERS = {"whoosh": "whooshes", "impact": "impacts", "pop": "pops"}
PRIORITY = ("impact", "whoosh", "pop")      # who wins when two cues collide
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}
VARIANTS = 3
WHOOSH_LEAD = 0.25     # the whoosh swells into the cut
MIN_GAP = 0.6          # seconds between any two cues
SAMPLE_RATE = 48000
# level of each kind in the SFX track (synth sounds peak around -3 dBFS; the voice averages ~-18 dBFS)
# measured live: at these levels SFX peaks sit ~7 dB under the voice's peaks (audible, never on top)
GAIN_DB = {"whoosh": -8.0, "impact": -3.0, "pop": -9.0}

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
    time: float        # seconds from the start of the video
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
        if w.word.rstrip("'\"”’»)").endswith((".", "!", "?", "…")):
            out.append(current)
            current = []
    return out + ([current] if current else [])


def _shock_hit(sentence: list[WordTiming], phrases: list[list[str]]) -> WordTiming | None:
    bare = [_bare(w.word) for w in sentence]
    for i in range(len(bare)):
        for phrase in phrases:
            if bare[i:i + len(phrase)] == phrase:
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

    candidates["whoosh"] = _keep([max(0.0, c - WHOOSH_LEAD) for c in sorted(cut_times)],
                                 rules.whoosh_cap, rules.whoosh_spacing)

    impacts = [0.0] if words else []  # the hook: the very first moment
    if rules.shock_words:
        phrases = [p.split() for p in SHOCK.get(script.lang, [])]
        for sentence in _sentences(words):
            hit = _shock_hit(sentence, phrases)
            if hit is not None and hit.start > 0:
                impacts.append(round(hit.start, 3))
    candidates["impact"] = _keep(impacts, rules.impact_cap, rules.impact_spacing)

    candidates["pop"] = _keep([round(w.start, 3) for w in words if _is_number(w.word)],
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


def ensure_library(root: Path) -> dict[str, list[Path]]:
    """Sounds per kind: the user's files if the folder has any, else synthesised ones (made once)."""
    library: dict[str, list[Path]] = {}
    for kind, folder in FOLDERS.items():
        d = root / folder
        d.mkdir(parents=True, exist_ok=True)
        files = sorted(p for p in d.iterdir() if p.suffix.lower() in AUDIO_EXTS)
        mine = [p for p in files if not p.name.startswith("synth_")]
        if mine:
            library[kind] = mine
            continue
        synth = [d / f"synth_{kind}_{k + 1}.wav" for k in range(VARIANTS)]
        for k, path in enumerate(synth):
            if not path.exists():
                ffmpeg.run(_synth_args(kind, k, path))
        library[kind] = synth
    return library


def build_sfx_track(cues: list[Cue], library: dict[str, list[Path]], duration: float, out: Path) -> Path | None:
    """All cues on one stereo track exactly `duration` long, or None when there is nothing to play."""
    cues = [c for c in cues if library.get(c.kind) and c.time < duration]
    if not cues:
        return None
    inputs: list[str] = []
    chains: list[str] = []
    for i, c in enumerate(cues):
        sounds = library[c.kind]
        inputs += ["-i", str(sounds[c.variant % len(sounds)])]
        ms = max(0, round(c.time * 1000))
        chains.append(f"[{i}:a]aresample={SAMPLE_RATE},aformat=channel_layouts=stereo,"
                      f"volume={GAIN_DB[c.kind]}dB,adelay={ms}|{ms}[s{i}]")
    mix = "".join(f"[s{i}]" for i in range(len(cues)))
    graph = ";".join(chains) + (f";{mix}amix=inputs={len(cues)}:duration=longest:normalize=0,"
                                f"apad=whole_dur={duration:.3f},atrim=end={duration:.3f}[out]")
    ffmpeg.run([*inputs, "-filter_complex", graph, "-map", "[out]", "-c:a", "pcm_s16le", str(out)])
    return out
