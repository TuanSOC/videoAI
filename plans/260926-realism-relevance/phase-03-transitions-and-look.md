---
phase: 3
title: Transitions And Look
status: in-progress
priority: P2
effort: 2h
dependencies: []
---

# Phase 3: Transitions And Look

## Overview
Cut within scenes, 0.18 s dissolve between scenes, no synthetic motion on video (images keep gentle Ken
Burns), subtle film grain on top of the existing grade.

## Requirements
- Functional: `TRANSITION = 0.18`; video shots: crop by focus.x, no zoompan (scale+crop only); image shots:
  zoompan push/pull/pan as now; punch-in for reused clip stays (static crop tighter, no zoom animation);
  grain `noise=alls=3:allf=t` after grade; vignette kept mild.
- Non-functional: render time not worse than now (removing zoompan on video should speed up).

## Architecture
`clips.py`: `_stream` branches: kind == "video" → `scale(oversize if punch)…,crop=w:h:x=(iw-ow)*fx` with
punch as larger scale factor; kind == "image" → current zoompan path. `Shot.motion` = "none" for video.
Add `GRAIN` constant appended after `GRADE`. `plan_shots` unchanged except motion for video.

## Related Code Files
- Modify: `src/vidgen/assemble/clips.py`
- Tests: `tests/test_assemble.py`

## Implementation Steps (TDD)
1. Tests first:
   - video shot args: no "zoompan", contains "noise=" and crop x from focus.
   - image shot args: contains "zoompan".
   - xfade duration 0.18 in graph; offset = (frames - round(0.18*fps))/fps.
   - punch-in video shot scales larger than non-punch shot.
   - pin: plan_shots frame totals, transition rules (no fade into color), crossfade continuity t0.
2. Implement.
3. Full suite; time one render (bach-tuoc) before/after.

## Success Criteria
- [ ] Tests pass; bach-tuoc render time ≤ previous 28 s.

## Risk Assessment
- Grain + x264 CRF 20 raises bitrate/file size; check final size stays reasonable (<40 MB for 60 s).
