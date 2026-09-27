---
title: 'Studio-grade retention: SFX, open loop, document highlighter'
description: >-
  Automatic sound design (synth whoosh/impact/pop), open-loop short scripts with
  a 3 s hook, document-highlight motion scenes
status: completed
priority: P2
branch: main
tags:
  - audio
  - sfx
  - script
  - motion
  - tdd
blockedBy: []
blocks: []
created: '2026-09-26T18:18:15.163Z'
createdBy: 'ck:plan'
source: skill
---

# Studio-grade retention: SFX, open loop, document highlighter

## Overview
Source: `plans/reports/brainstorm-260927-retention-sound-motion.md`. Make shorts feel produced rather than
generated: tasteful sound design, a script structure that holds attention to the end, and a document-style
evidence scene. **Mode: TDD** — per phase: failing tests for new behaviour + pinned current behaviour →
implement → full suite green (210 baseline). Tests never call Ollama/Pexels/Wikipedia/edge-tts.

**This round implements Phase 1 only.** Phases 2-3 are specified here for later.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [SFX Engine](./phase-01-sfx-engine.md) | Completed |
| 2 | [Open Loop Prompts](./phase-02-open-loop-prompts.md) | Completed |
| 3 | [Document Highlighter](./phase-03-document-highlighter.md) | Completed |

## Out of scope
UI controls for SFX, AI music/SFX generation, downloading SFX packs, real-publication imitation.

## Dependencies
None blocking. Touches `assemble/render.py` (two separate filter graphs from the NaN fix must stay) and
`assemble/clips.py` (plan_shots cut times).

## Review fixes (2026-09-27)
Phase 1 revised after the full review: synth sounds normalised to −3 dBFS peak (pops were −23), cues are
PEAK times (whoosh mid-dissolve), graph via file (hundreds of cues), unreadable user files skipped.
