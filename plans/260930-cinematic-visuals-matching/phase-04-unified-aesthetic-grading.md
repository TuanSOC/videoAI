---
phase: 4
title: "Unified aesthetic grading"
status: pending
priority: P2
effort: "0.5d"
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
