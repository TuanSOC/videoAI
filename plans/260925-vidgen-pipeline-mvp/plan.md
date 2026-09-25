---
title: AI Video Generation Pipeline MVP
description: >-
  Local Python CLI: topic → realistic faceless video (short 9:16 / long 16:9,
  vi/en) at $0 API cost via hybrid stock + local AI visuals
status: in-progress
priority: P2
branch: ''
tags:
  - python
  - ffmpeg
  - ai-video
  - tts
  - cli
blockedBy: []
blocks: []
created: '2026-09-25T06:53:38.497Z'
createdBy: 'ck:plan'
source: skill
---

# AI Video Generation Pipeline MVP

## Overview
Greenfield Python CLI `vidgen`. Input topic → output `final.mp4` + `metadata.json` for manual review & upload.
Source: [brainstorm report](../reports/brainstorm-260925-video-gen-pipeline.md).

```
topic → script.json → voice (per-scene mp3 + word timings) → visuals (stock → Flux → Wan 2.2) → FFmpeg assemble → final.mp4 + metadata.json
```

Key decisions:
- Hybrid visuals: stock footage primary (Pexels/Pixabay), AI fallback (Flux-schnell image, Wan 2.2 video via local ComfyUI API), capped AI-video count per video.
- LLM: Gemini free tier (Flash) primary, Ollama local fallback. Output validated by Pydantic.
- TTS: edge-tts with WordBoundary events → word timings for free; faster-whisper only as fallback aligner (Piper TTS fallback path).
- Per-scene TTS → scene duration = audio duration (no fragile global alignment).
- Every stage writes artifact in `output/<slug>/`; pipeline resumes by skipping existing artifacts → enables `--review` pause.
- FFmpeg with NVENC (fallback libx264).

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Project Setup](./phase-01-project-setup.md) | Completed |
| 2 | [Script Generation](./phase-02-script-generation.md) | In Progress |
| 3 | [Voice and Timing](./phase-03-voice-and-timing.md) | Completed |
| 4 | [Visual Sourcing](./phase-04-visual-sourcing.md) | In Progress |
| 5 | [Video Assembly](./phase-05-video-assembly.md) | Completed |
| 6 | [CLI and Review Flow](./phase-06-cli-and-review-flow.md) | Completed |
| 7 | [End-to-End Testing](./phase-07-end-to-end-testing.md) | Pending |

Order: 1 → 2 → 3 → (4 ∥ 5 partially: 5 can start with placeholder color clips) → 6 → 7.
Total effort ≈ 6-8 days.

## Acceptance (MVP)
- `python -m vidgen make "<topic>" --format short|long --lang vi|en` → MP4 for all 4 combos
- Caption drift ≤0.2s; no watermark; only free-commercial assets
- Short render <10 min, long <45 min on 12GB NVIDIA GPU
- $0 API cost

## Out of scope
Auto-upload, trend discovery, web UI, voice clone, avatars, SaaS, finance/news source grounding (phase 2 project).

## Dependencies
None (no other plans).
