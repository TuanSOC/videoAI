---
phase: 1
title: "Contextual Transition Engine"
status: planned
priority: P1
effort: "0.5d"
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
