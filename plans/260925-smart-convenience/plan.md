---
title: 'Smarter & faster: relevant clips, fact-check, speed, convenience'
description: ''
status: completed
priority: P2
branch: main
tags: []
blockedBy: []
blocks: []
created: '2026-09-25T09:38:11.100Z'
createdBy: 'ck:plan'
source: skill
---

# Smarter & faster: relevant clips, fact-check, speed, convenience

## Overview
From a feature review on the user's 4 real videos (2026-09-25):
- Clips ignore relevance: octopus video ~2-3/11 scenes show an octopus; "octopus in cold water" → "a person cooking an octopus"; "octopus moving between caves" → Nemrut Dağı caves. `selector.score` only rates orientation/resolution/length.
- No fact check after scripting; wrong claims slip through.
- Scripts short: 36s / 50s / 49.5s (target 45-75, mid 60); "rewrite longer" retry ineffective on 8B.
- Slow: voice 45-49s for ~10 scenes (one TTS request per scene, sequential); visuals 40-120s (sequential downloads).
- Many wait-and-click gates; no notification; one-by-one renders.

Decisions:
1. Relevance from clip descriptions (Pexels url slug / photo alt, Pixabay tags) + video subject (dominant word across visual queries) mandatory where the scene query mentions it; subject-keeping fallback queries; LLM judge only when the best match is weak.
2. Voice: one edge-tts request for the whole script, words aligned back to scenes; fall back to per-scene on any mismatch. Downloads in a thread pool.
3. Length: "expand the draft" (keep scenes, add new ones from unused facts) instead of "rewrite longer"; UI "Kéo dài".
4. Fact-check: LLM compares each scene to sources → `Scene.flag` note, shown as ⚠; auto after scripting + manual re-check.
5. Rewrite one scene: synchronous LLM call returns a new narration into the unsaved draft.
6. One-click: "Chọn góc & tạo video luôn", "Render tất cả", browser notifications, tab-title progress.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Relevant Clip Selection](./phase-01-relevant-clip-selection.md) | Completed |
| 2 | [Speed (voice single request + parallel downloads)](./phase-02-speed-voice-single-request-parallel-downloads.md) | Completed |
| 3 | [Length Expansion](./phase-03-length-expansion.md) | Completed |
| 4 | [Fact-Check Flags](./phase-04-fact-check-flags.md) | Completed |
| 5 | [Rewrite One Scene](./phase-05-rewrite-one-scene.md) | Completed |
| 6 | [One-Click Flow and Notifications](./phase-06-one-click-flow-and-notifications.md) | Completed |

## Dependencies

<!-- Cross-plan dependencies -->
