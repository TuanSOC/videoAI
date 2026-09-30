---
phase: 2
title: "Quality-gated selection"
status: in-progress
priority: P1
effort: "0.5d"
dependencies: [1]
---

# Phase 2: Quality-gated selection

## Overview
With an AI generator available, a stock clip must score ≥ `vision.ai_bar` (7) or the scene is drawn by AI from
its ai_prompt; only if AI fails does a 5-6 clip come back. Without a generator nothing changes.

## Requirements
- config `vision.ai_bar: 7` (0 = off). `Selector.pick` for stock scenes when `self.ai` is set:
  stock video ≥ bar → stock photo ≥ bar → AI image → stock ≥ MIN_VISION(5) → weak → placeholder.
- Judged clips are reused between the two passes (no extra vision calls, no new downloads of rejected ones).
- Scenes the script marks ai_image/ai_video keep their current order.

## Tests
- fake AI + fake vision: best stock 6 → AI asset; best stock 8 → stock; AI failing → the 6 clip; no AI → the 6 clip.

## Success Criteria
- [ ] Unit tests above; suite green. Live check waits for phase 3 (no generator on this machine).
