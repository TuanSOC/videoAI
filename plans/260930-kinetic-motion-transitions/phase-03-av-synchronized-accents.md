---
phase: 3
title: Audio-Visual Synchronized Accents
status: completed
priority: P2
effort: 0.5d
dependencies:
  - 1
---

# Phase 3: Audio-Visual Synchronized Accents

## Overview
Impact cues mostly land mid-shot (the hook at 0 s, shock words), not on scene changes, so phase 1's
zoomin/fadeblack rarely catches them. Decision (user, 2026-09-30): a light punch-zoom instead of the white flash
in the original report — reads as emphasis, not cheap, no strobe risk.

## Requirements
- `render.punch_times(cues, cuts)`: impact cues farther than `CUE_WINDOW` from every scene change.
- `render.punch_filter(times, preset)`: `zoompan` over the joined video, zoom `1 + PUNCH·env`, env = quarter-sine
  rise over `PUNCH_ATTACK` (0.1 s) then half-cosine fall over `PUNCH_RELEASE` (0.4 s); `PUNCH` = 0.04.
- `final_args(video_filter=…)`: the punch runs before `ass`, so captions never zoom.

## Results (2026-09-30)
- Static pattern, impact at 0.20 s: zoom 1.000 → 1.0425 at 0.30 s → 1.0075 at 0.60 s → 1.000 by 0.70 s.
- Black-hole short: impacts at 0.0 s (hook) and 57.3 s punched; render 56 s (no measurable cost), -14.2 LUFS.
- 365 tests green.
