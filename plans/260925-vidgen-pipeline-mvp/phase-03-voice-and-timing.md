---
phase: 3
title: Voice and Timing
status: completed
priority: P1
effort: 1d
dependencies:
  - 2
---

# Phase 3: Voice and Timing

## Overview
Per-scene TTS audio + word-level timings; scene durations derived from audio.

## Requirements
- Functional: `voice/scene_XXX.mp3` per scene, `timings.json` (scene durations + word timings, global offsets), `voice.mp3` concatenated.
- Non-functional: caption drift ≤0.2s; concurrent TTS (asyncio, limit 4).

## Architecture
```
voice/
  tts.py     # TTSProvider protocol: synth(text, voice, out) -> list[WordTiming] | None
             # EdgeTTS (boundary="WordBoundary" → offsets), PiperTTS fallback (returns None)
  align.py   # faster-whisper word_timestamps for scenes with no timings (fallback path)
  builder.py # run all scenes, measure durations (ffprobe), add 0.15s gap, build global timeline
```
Flow: for each scene → synth → timings? else align.py → duration = ffprobe(mp3) + gap → global offset = cumulative sum.

## Related Code Files
- Create: `src/vidgen/voice/{__init__,tts,align,builder}.py`, `tests/test_voice_timeline.py`

## Implementation Steps
1. EdgeTTS wrapper using `edge_tts.Communicate(text, voice, boundary="WordBoundary")`, stream → audio bytes + WordBoundary events (offset/duration in 100ns units → seconds).
2. Piper fallback (optional voice models per lang in config); if missing, error with clear message.
3. faster-whisper aligner (model `small`, cuda, float16), `word_timestamps=True`, language from script.
4. Builder: parallel synth, durations via ffprobe, global timeline, concat to `voice.mp3` (ffmpeg concat with silence gap).
5. Resolve edge-tts vi WordBoundary gaps: if events missing for vi voice, auto-run aligner.
6. Tests: timeline offset math with synthetic timings.

## Success Criteria
- [x] vi + en scripts produce voice + timings (vi live-tested; en WordBoundary verified live)
- [x] Drift ≤0.2s — cross-checked 35 vi words vs whisper: median 0.03s, max 0.16s
- [x] Killing edge-tts (offline) triggers clear error (3 retries, then TTSError)

## Implementation Notes
- Piper fallback DEFERRED (YAGNI): edge-tts failure → clear TTSError. Revisit if edge-tts gets blocked.
- Scenes decoded to padded PCM WAV before measuring → sample-exact durations, no cumulative drift.
- faster-whisper CUDA fails on this machine (missing cublas64_12.dll) → auto CPU int8 fallback at transcribe time (~7s for 13s audio).
- Shared `src/vidgen/ffmpeg.py` (run, duration) for reuse in assembly.

## Risk Assessment
- edge-tts is unofficial; Microsoft may block → Piper/Kokoro fallback, pin version.
- WordBoundary may be unsupported for some voices → whisper align fallback.
