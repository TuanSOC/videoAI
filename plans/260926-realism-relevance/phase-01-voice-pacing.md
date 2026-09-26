---
phase: 1
title: Voice Pacing
status: completed
priority: P1
effort: 2h
dependencies: []
---

# Phase 1: Voice Pacing

## Overview
Cut scene boundaries tight to speech: ≤0.08 s before first word, ≤0.2 s after last word (was mid-pause
+ 0.15 pad → 1.0–1.4 s silence). Configurable speaking rate for edge-tts.

## Requirements
- Functional: inter-scene silence ≈0.25–0.35 s; `voice.rate` config (default "+8%"), per-video voice
  override stays via existing settings; word timings stay aligned after trimming.
- Non-functional: never clip the tail of a word; resume logic (sidecar = done marker) unchanged.

## Architecture
`builder.py`: `cut_point` currently returns midpoint. Replace boundary logic with a pair:
`scene_bounds(prev_end, next_start) -> (end_of_prev, start_of_next)`:
`end = min(mid, prev_end + TAIL)`, `start = max(mid, next_start - LEAD)`, overlap → both = prev_end.
Piece start/end built from these; first scene start = max(0, first_word - LEAD); last scene end =
last_word + TAIL. `GAP_SECONDS` pad reduced to 0 (tail already included) — keep constant, set 0.05.
`tts.py`: pass `rate=settings.voice.rate` to `edge_tts.Communicate`.

## Related Code Files
- Modify: `src/vidgen/voice/builder.py`, `src/vidgen/voice/tts.py`, `src/vidgen/config.py`, `config.yaml`
- Tests: `tests/test_voice_timeline.py`

## Implementation Steps (TDD)
1. Tests first:
   - `scene_bounds(1.0, 2.2)` → (1.2, 2.12); `scene_bounds(1.0, 1.1)` → (1.05, 1.05); overlap case.
   - piece planning for a 2-scene group: first piece starts ≤0.08 s before first word; words shifted by
     the same offset (local word start ≥ 0).
   - `Communicate` receives rate from settings (monkeypatched fake).
   - pin: existing split_by_scene / plan_groups tests still pass.
2. Implement `scene_bounds`, use it where `cut_point` is used; keep `cut_point` removed (update callers).
3. Add `voice.rate` to config model + yaml.
4. Full suite.

## Success Criteria
- [ ] New tests pass; old voice tests pass.
- [ ] Phase 7 measures ≤0.4 s gaps.

## Risk Assessment
- edge-tts word `end` slightly early → TAIL 0.2 s covers release; verify by ear in phase 7.
- Voice cache: rate change invalidates cached groups? Cache key must include rate → check `tts.py` cache key, add rate.
