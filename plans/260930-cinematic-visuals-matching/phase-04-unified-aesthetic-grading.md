---
phase: 4
title: Unified aesthetic grading
status: completed
priority: P2
effort: 0.5d
dependencies: []
---

# Phase 4: Unified aesthetic grading

## Overview
One look per video instead of the single mild GRADE, so clips from different photographers read as one film.

## Requirements
- `looks` in config: dark_mystery (cool shadows, high contrast, deep vignette), cyber_tech (teal/orange, neon
  lift), vintage_archive (warm sepia, finer grain), neutral (today's GRADE).
- Script `look` chosen by the writer (like `mood`), overridable in the studio; clips.py applies it to every
  stock/AI shot (not document cards).
- Frame check on 3 topics; render time unchanged (filters are per-segment, already re-encoded).

## Results (2026-09-30)
- `assemble/looks.py`: neutral (== the old GRADE byte for byte), dark_mystery, cyber_tech, vintage_archive —
  tuned on real stock frames (curves' "vintage" preset turned shadows magenta → a 50 % sepia channel mix instead;
  first cyber_tech too subtle, first dark_mystery crushed dark clips → both retuned).
- Writer picks `look` like `mood` (prompts list $looks); `Script.look`; series `look:` pins the channel look
  (state.json, validated); evidence cards always neutral. cyber-security.yaml → cyber_tech.
- Live: WannaCry EN re-render with cyber_tech: consistent teal shadows / warm highlights, card paper untouched,
  52 s render, -14.3 LUFS, yuv420p. 406 tests green.
- Not done: a studio control to change the look (edit script.json `look` or state.json for now).
