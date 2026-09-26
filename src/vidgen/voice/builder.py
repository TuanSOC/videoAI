"""Script → voice.wav + timeline.json with global word timings.

Scenes are voiced in groups (one TTS request per ~60 words, 4 in parallel): one request per scene
was 3-4x slower and bursts of small requests got throttled. Word boundaries are matched back to their
scenes and each group's audio is cut tight to the speech: a little before the first word and after
the last one, so the TTS's long sentence pauses don't pile up into 1s+ silences between scenes. If a group's words
can't be matched (unusual tokenisation), that group falls back to one request per scene.

Every scene ends up as a padded PCM WAV measured sample-exactly, so caption timing cannot drift
across 100+ scenes the way summed MP3 estimates would.
"""

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import TypeAdapter

from vidgen import ffmpeg
from vidgen.config import Settings
from vidgen.fsutil import write_atomic
from vidgen.models import Scene, SceneAudio, Script, Timeline, WordTiming
from vidgen.text import align, bare
from vidgen.voice.tts import TTSError, synth_edge

log = logging.getLogger(__name__)
WORDS = TypeAdapter(list[WordTiming])
GAP_SECONDS = 0.05   # pad after each scene (on top of TAIL)
LEAD = 0.08          # silence kept before a scene's first word
TAIL = 0.2           # ... and after its last word (word ends are reported a little early)
SAMPLE_RATE = 24000
MAX_GROUP_WORDS = 60   # a short (~160 words) → 3-4 parallel requests
GROUP_CONCURRENCY = 4  # measured: 4 concurrent grouped requests, no throttling (per-scene bursts were)

Synth = Callable[[str, str, Path], Awaitable[list[WordTiming]]]
Aligner = Callable[[Path, str], list[WordTiming]]


def build_timeline(scene_ids: list[int], paths: list[str], durations: list[float],
                   words: list[list[WordTiming]]) -> Timeline:
    """Lay scenes end to end; shift each scene's local word times by its global start."""
    scenes: list[SceneAudio] = []
    cursor = 0.0
    for sid, path, dur, ws in zip(scene_ids, paths, durations, words, strict=True):
        shifted = [w.model_copy(update={"start": w.start + cursor, "end": w.end + cursor}) for w in ws]
        scenes.append(SceneAudio(scene_id=sid, path=path, start=cursor, duration=dur, words=shifted))
        cursor += dur
    return Timeline(scenes=scenes)


def scene_bounds(prev_end: float, next_start: float) -> tuple[float, float]:
    """(end of the previous scene, start of the next) inside the pause between them: at most TAIL after
    the last word and LEAD before the next one; a short pause is cut in the middle; words that overlap
    are never cut into."""
    mid = (prev_end + next_start) / 2 if next_start >= prev_end else prev_end
    return min(mid, prev_end + TAIL), max(mid, next_start - LEAD)


def _piece(src: Path, words: list[WordTiming], start: float | None = None, end: float | None = None) -> "Piece":
    """Speech span of one scene; start/end default to LEAD/TAIL around its words."""
    if not words:
        return Piece(src, 0.0, None, words)
    return Piece(src, max(0.0, words[0].start - LEAD) if start is None else start,
                 words[-1].end + TAIL if end is None else end, words)


def plan_groups(scenes: list[Scene], max_words: int = MAX_GROUP_WORDS) -> list[list[Scene]]:
    """Consecutive scenes packed up to max_words (a longer scene gets a group of its own)."""
    groups: list[list[Scene]] = []
    current: list[Scene] = []
    count = 0
    for sc in scenes:
        n = len(sc.narration.split())
        if current and count + n > max_words:
            groups.append(current)
            current, count = [], 0
        current.append(sc)
        count += n
    if current:
        groups.append(current)
    return groups


def split_by_scene(scenes: list[Scene], words: list[WordTiming]) -> list[list[WordTiming]] | None:
    """Assign a group's word boundaries to its scenes. TTS may split one token into several boundaries
    ("AI-generated") or merge several tokens into one ("năm 2013,"); text.align pairs them up.
    None when they don't line up, or a boundary spans two scenes — the caller then voices those scenes
    one by one."""
    words = [w for w in words if bare(w.word)]  # punctuation-only boundaries carry no word
    tokens: list[str] = []
    owner: list[int] = []
    for k, sc in enumerate(scenes):
        for t in sc.narration.split():
            tokens.append(t)
            owner.append(k)
    groups = align(tokens, [w.word for w in words])
    if groups is None:
        return None
    out: list[list[WordTiming]] = [[] for _ in scenes]
    for token_ids, word_ids in groups:
        scene_ids = {owner[t] for t in token_ids}
        if len(scene_ids) != 1:
            return None
        out[scene_ids.pop()] += [words[k] for k in word_ids]
    return out if all(out) else None


@dataclass
class Piece:
    """Where one scene's speech lives: [start, end) of a source MP3 (end None = to the end)."""
    src: Path
    start: float
    end: float | None
    words: list[WordTiming]  # relative to the source file


def _cut(piece: Piece, wav: Path) -> None:
    trim = f"atrim=start={piece.start:.3f}" + (f":end={piece.end:.3f}" if piece.end is not None else "")
    ffmpeg.run(["-i", str(piece.src), "-af", f"{trim},asetpts=PTS-STARTPTS,apad=pad_dur={GAP_SECONDS}",
                "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(wav)])


def _save_scene(voice_dir: Path, scene: Scene, piece: Piece) -> None:
    """Write scene_NNN.wav + its word sidecar (words local to the scene). Sidecar last = 'done' marker."""
    wav = voice_dir / f"scene_{scene.id:03d}.wav"
    _cut(piece, wav)
    local = [w.model_copy(update={"start": max(0.0, w.start - piece.start), "end": max(0.0, w.end - piece.start)})
             for w in piece.words]
    # atomic: a half-written sidecar would count as "done" on resume and then fail to parse
    write_atomic(voice_dir / f"scene_{scene.id:03d}.words.json", WORDS.dump_json(local))


def _done(voice_dir: Path, scene: Scene) -> bool:
    return (voice_dir / f"scene_{scene.id:03d}.wav").exists() and \
        (voice_dir / f"scene_{scene.id:03d}.words.json").exists()


async def _voice_group(group: list[Scene], voice: str, voice_dir: Path, synth: Synth,
                       sem: asyncio.Semaphore) -> None:
    todo = [sc for sc in group if not _done(voice_dir, sc)]  # resume: finished scenes are kept
    if not todo:
        return
    label = f"scenes {todo[0].id}-{todo[-1].id}" if len(todo) > 1 else f"scene {todo[0].id}"
    async with sem:
        split = None
        if len(todo) > 1:
            mp3 = voice_dir / f"group_{todo[0].id:03d}.mp3"
            try:
                words = await synth(" ".join(sc.narration for sc in todo), voice, mp3)
            except TTSError as e:
                raise TTSError(f"{label}: {e}") from e
            split = split_by_scene(todo, words)
            if split is None:
                log.info("%s: word boundaries didn't line up, voicing scene by scene", label)
        if split is not None:
            for k, (sc, ws) in enumerate(zip(todo, split)):
                start = scene_bounds(split[k - 1][-1].end, ws[0].start)[1] if k else None
                end = scene_bounds(ws[-1].end, split[k + 1][0].start)[0] if k + 1 < len(todo) else None
                await asyncio.to_thread(_save_scene, voice_dir, sc, _piece(mp3, ws, start, end))
            return
        for sc in todo:
            mp3 = voice_dir / f"scene_{sc.id:03d}.mp3"
            try:
                words = await synth(sc.narration, voice, mp3)
            except TTSError as e:
                raise TTSError(f"scene {sc.id}: {e}") from e
            await asyncio.to_thread(_save_scene, voice_dir, sc, _piece(mp3, words))


async def _voice_all(script: Script, voice: str, voice_dir: Path, synth: Synth) -> None:
    sem = asyncio.Semaphore(GROUP_CONCURRENCY)
    await asyncio.gather(*(_voice_group(g, voice, voice_dir, synth, sem) for g in plan_groups(script.scenes)))


def _default_aligner(s: Settings) -> Aligner:
    def aligner(path: Path, lang: str) -> list[WordTiming]:
        from vidgen.voice.align import align

        return align(path, lang, s.pipeline.whisper)

    return aligner


def generate_voice(script: Script, out_dir: Path, s: Settings,
                   synth: Synth | None = None, aligner: Aligner | None = None) -> Timeline:
    voice_dir = out_dir / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    voice = s.pipeline.voices[script.lang]
    aligner = aligner or _default_aligner(s)
    synth = synth or functools.partial(synth_edge, rate=s.pipeline.voice_rate)

    asyncio.run(_voice_all(script, voice, voice_dir, synth))

    ids, wav_names, durations, words = [], [], [], []
    for scene in script.scenes:
        wav = voice_dir / f"scene_{scene.id:03d}.wav"
        ws = WORDS.validate_json((voice_dir / f"scene_{scene.id:03d}.words.json").read_bytes())
        if not ws:
            log.info("scene %d: no TTS word timings, aligning with whisper", scene.id)
            ws = aligner(wav, script.lang)
        ids.append(scene.id)
        wav_names.append(f"voice/{wav.name}")
        durations.append(ffmpeg.duration(wav))
        words.append(ws)

    (voice_dir / "concat.txt").write_text(
        "".join(f"file '{Path(n).name}'\n" for n in wav_names), encoding="utf-8")
    ffmpeg.run(["-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", "../voice.wav"], cwd=voice_dir)

    timeline = build_timeline(ids, wav_names, durations, words)
    write_atomic(out_dir / "timeline.json", timeline.model_dump_json(indent=2))
    return timeline
