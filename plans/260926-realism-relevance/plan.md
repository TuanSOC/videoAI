---
title: 'Realism, relevance, light transitions, clean subtitles'
description: >-
  Vision-judged clip choice (qwen2.5vl), tight voice pacing, cut+short-dissolve
  editing, 3-6 word one-line subtitles, mood music
status: completed
priority: P2
branch: main
tags:
  - visuals
  - voice
  - subtitles
  - render
  - tdd
blockedBy: []
blocks: []
created: '2026-09-26T15:14:12.294Z'
createdBy: 'ck:plan'
source: skill
---

# Realism, relevance, light transitions, clean subtitles

## Overview
Source: `plans/reports/brainstorm-260926-realism-relevance.md`. Measured on `output/phishing-…-260926`:
wrong clips in scenes 1/6/7/8 (lossy fallback queries + slug-only relevance), 1.0–1.4 s silence at every
scene boundary, muddy 0.3 s cross-fades + fake zoom on moving video, busy 1–3 word karaoke subtitles, no music.

**Mode: TDD.** Every phase: write failing tests for the new behaviour + pin current behaviour that must
survive → implement → full suite green (162 tests baseline). Tests never call Ollama/Pexels/Wikipedia.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Voice Pacing](./phase-01-voice-pacing.md) | Completed |
| 2 | [Subtitle Chunks](./phase-02-subtitle-chunks.md) | Completed |
| 3 | [Transitions And Look](./phase-03-transitions-and-look.md) | Completed |
| 4 | [Queries And Thumbnails](./phase-04-queries-and-thumbnails.md) | Completed |
| 5 | [Vision Judge](./phase-05-vision-judge.md) | Completed |
| 6 | [Mood Music](./phase-06-mood-music.md) | Completed |
| 7 | [Live Verification](./phase-07-live-verification.md) | Completed |

Order: 1→2→3 independent, cheap, visible wins. 4 → 5 (vision needs thumbs + query set). 6 independent.
7 last.

## Acceptance (whole plan)
- Re-render phishing video: scenes 1/6/7/8 relevant (vision score ≥6).
- No inter-scene silence > 0.4 s.
- Subtitle chunks 3–6 words (fewer only for a shorter sentence), ≤26 chars, never end on function word.
- Added pipeline time ≤ 4 min per short video.
- Full suite green; no live calls in tests.

## Out of scope
New TTS engine, AI video, auto music download, long-format tuning.

## Dependencies
None blocking. `260925-vidgen-pipeline-mvp` (in-progress: E2E + README) is unaffected; README should later
mention music mood folders and the qwen2.5vl pull.
