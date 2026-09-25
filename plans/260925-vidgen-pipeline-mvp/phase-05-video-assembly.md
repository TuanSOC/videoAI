---
phase: 5
title: Video Assembly
status: completed
priority: P1
effort: 1.5d
dependencies:
  - 3
  - 4
---

# Phase 5: Video Assembly

## Overview
FFmpeg composes scene clips + voice + music + burned captions into `final.mp4`, then generates `metadata.json`.

## Requirements
- Functional: 9:16 1080x1920 and 16:9 1920x1080, 30fps, H.264 + AAC; captions styled per format; background music ducked under voice.
- Non-functional: NVENC when available; short <10 min, long <45 min total pipeline.

## Architecture
```
assemble/
  clips.py      # per scene → normalized segment: scale+crop to fill, trim/loop to duration, photos → zoompan Ken Burns, 0.3s crossfade optional (off in MVP)
  subtitles.py  # timings → ASS: short = 1-3 words centered, big bold, active-word highlight; long = bottom 2-line sentence chunks
  music.py      # pick random track from assets/music/<mood>/, loop to length
  render.py     # concat segments (concat demuxer) → final mux: voice + music (sidechaincompress ducking, music -18dB) + ass burn-in
metadata.py     # LLM: title (≤70 chars), description (+ asset credits, AI disclosure line), 5-15 hashtags/tags → metadata.json
```
Final mux filter sketch:
```
[1:a]asplit[v1][v2];[2:a]volume=0.25[m];[m][v1]sidechaincompress=threshold=0.05:ratio=8[md];[v2][md]amix=inputs=2[a];
[0:v]ass=subs.ass[v]
```
`segments/` kept for resume; delete with `--clean`.

## Related Code Files
- Create: `src/vidgen/assemble/{__init__,clips,subtitles,music,render}.py`, `src/vidgen/metadata.py`, `tests/test_subtitles.py`, `tests/test_ffmpeg_args.py`

## Implementation Steps
1. `clips.py`: build ffmpeg args per asset kind; render segments in parallel (2-3 workers; NVENC session limits).
2. `subtitles.py`: ASS header per format, font from `assets/fonts/`, karaoke highlight via `\k` or per-word dialogue lines.
3. `music.py`: user drops Pixabay/YT Audio Library tracks into `assets/music/`; none → no music (warn).
4. `render.py`: concat + final mux, `-movflags +faststart`.
5. `metadata.py`: reuse LLM provider from phase 2 (DRY).
6. Tests: ASS generation snapshot, ffmpeg arg builders (no actual render).

## Success Criteria
- [x] Short and long render with correct resolution/fps (ffprobe: 1080x1920 / 1920x1080, 30fps, AAC 48k stereo)
- [x] Captions synced, Vietnamese diacritics render correctly (frames inspected); video dur within 16ms of audio
- [~] Music ducking path renders; loudness -13.7 LUFS. Subjective mix check pending real music track (user supplies)
- [x] `metadata.json` has title/description/tags + credits + AI disclosure (unit-tested with fake LLM; live LLM pending .env)

## Implementation Notes
- NVENC unavailable: NVIDIA driver API 12.2 < FFmpeg 8.1 requirement (driver ≥570). `ffmpeg.video_encoder()` probes with a real encode → libx264 veryfast. Doctor now uses the same probe. Short (17s) render ≈4s with libx264.
- `frame_counts` chains boundaries from previous scene end (fixed 1-frame loss when float sums land on .5).
- Captions: TTS words lack punctuation → restored from narration when token counts match 1:1.
- Fonts copied into output dir so `ass` filter uses relative `fontsdir=fonts` (repo path has a space).
- Final pass reads segments via concat demuxer directly (no intermediate video file).

## Risk Assessment
- `ass` filter on Windows path escaping (`C\:/...`) → use relative paths with cwd = output dir.
- Long video with ~100 segments: concat demuxer requires identical codec params → normalize all segments with same encoder settings.
