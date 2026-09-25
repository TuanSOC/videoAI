"""Per-scene TTS → padded WAVs → one voice.wav + timeline.json with global word timings.

Each scene is decoded to PCM before measuring, so durations are sample-exact and
caption timing cannot drift across 100+ scenes the way summed MP3 estimates would.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import TypeAdapter

from vidgen import ffmpeg
from vidgen.config import Settings
from vidgen.fsutil import write_atomic
from vidgen.models import SceneAudio, Script, Timeline, WordTiming
from vidgen.voice.tts import TTSError, synth_edge

log = logging.getLogger(__name__)
WORDS = TypeAdapter(list[WordTiming])
GAP_SECONDS = 0.15
SAMPLE_RATE = 24000
CONCURRENCY = 1  # sequential requests prevent edge-tts websocket throttling

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


async def _synth_all(script: Script, voice: str, out_dir: Path, synth: Synth) -> list[list[WordTiming]]:
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(scene) -> list[WordTiming]:
        mp3 = out_dir / f"scene_{scene.id:03d}.mp3"
        sidecar = mp3.with_suffix(".words.json")
        if mp3.exists() and sidecar.exists():  # finished in an earlier, partly failed run
            return WORDS.validate_json(sidecar.read_bytes())
        async with sem:
            try:
                words = await synth(scene.narration, voice, mp3)
            except TTSError as e:
                raise TTSError(f"scene {scene.id}: {e}") from e
        sidecar.write_bytes(WORDS.dump_json(words))
        return words

    return await asyncio.gather(*(one(s) for s in script.scenes))


def _default_aligner(s: Settings) -> Aligner:
    def aligner(path: Path, lang: str) -> list[WordTiming]:
        from vidgen.voice.align import align

        return align(path, lang, s.pipeline.whisper)

    return aligner


def generate_voice(script: Script, out_dir: Path, s: Settings,
                   synth: Synth = synth_edge, aligner: Aligner | None = None) -> Timeline:
    voice_dir = out_dir / "voice"
    voice_dir.mkdir(parents=True, exist_ok=True)
    voice = s.pipeline.voices[script.lang]
    aligner = aligner or _default_aligner(s)

    words = asyncio.run(_synth_all(script, voice, voice_dir, synth))

    ids, wav_names, durations = [], [], []
    for i, (scene, ws) in enumerate(zip(script.scenes, words, strict=True)):
        mp3 = voice_dir / f"scene_{scene.id:03d}.mp3"
        wav = mp3.with_suffix(".wav")
        ffmpeg.run(["-i", str(mp3), "-af", f"apad=pad_dur={GAP_SECONDS}",
                    "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(wav)])
        if not ws:
            log.info("scene %d: no TTS word timings, aligning with whisper", scene.id)
            words[i] = aligner(mp3, script.lang)
        ids.append(scene.id)
        wav_names.append(f"voice/{wav.name}")
        durations.append(ffmpeg.duration(wav))

    (voice_dir / "concat.txt").write_text(
        "".join(f"file '{Path(n).name}'\n" for n in wav_names), encoding="utf-8")
    ffmpeg.run(["-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", "../voice.wav"], cwd=voice_dir)

    timeline = build_timeline(ids, wav_names, durations, words)
    write_atomic(out_dir / "timeline.json", timeline.model_dump_json(indent=2))
    return timeline
