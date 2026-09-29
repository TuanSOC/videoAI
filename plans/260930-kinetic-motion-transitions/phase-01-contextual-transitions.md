---
phase: 1
title: Contextual Transition Engine
status: completed
priority: P1
effort: 0.5d
dependencies: []
---

# Phase 1: Contextual Transition Engine

## Overview
Replace hardcoded `xfade=transition=fade` with dynamic selection from FFmpeg's 57 native transitions based on audio cues and scene context.

## Requirements
- `src/vidgen/assemble/transitions.py`:
  - `pick_transition(prev: Shot, cur: Shot, cue: Cue | None, last: str) -> str`
  - Mapping:
    - Document scenes (`source="document"`) -> `slideup`
    - `whoosh` cue -> `smoothleft`, `smoothright`, `wipetl`
    - `impact` cue -> `zoomin` or `fadeblack`
    - Short shots (<1.4s) -> straight cuts (no transition)
    - Fallback -> `dissolve`
    - Anti-repetition: never repeat the same non-dissolve transition back-to-back.
- Integration:
  - Update `shot_args(shot, nxt, p, out, transition_name)` in `clips.py`.
- Tests:
  - `tests/test_transitions.py`: verifies selection rules, anti-repetition, document mapping, and cue synchronization.

## Results (2026-09-30)
- `assemble/transitions.py`: `pick_transition(prev, cur, cue, last)` + `assign_transitions(shots, cues, fps)`;
  `Shot.transition` / `Shot.card`; `shot_args()` uses `nxt.transition`. Ken Burns progress eased `(1-cos(πx))/2`
  (pulled forward from phase 2).
- Deviations from the spec (code facts): cues were computed AFTER segments rendered → `render_segments(cues_for=…)`
  plans sound from the shot plan's cut times first; default stays `fade` (xfade `dissolve` is a grainy pixel
  dither); choice is deterministic from `cue.variant`, not `random`; the <1.4 s straight-cut rule lives in
  `plan_shots` (cut times must be known before cues). Anti-repetition compares with the last *move* (fades
  between two whooshes gave 3× smoothright live).
- Verified: all 7 xfade names + eased zoompan run on FFmpeg 8.1; Voyager short re-render 53 s; whooshes at
  5.9/16.9/45.4 s land on directional wipes, the evidence card slides up; 360 tests green.
