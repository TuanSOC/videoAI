---
phase: 2
title: S-Curve Eased Ken Burns & Static Drift
status: completed
priority: P1
effort: 0.5d
dependencies:
  - 1
---

# Phase 2: S-Curve Eased Ken Burns & Static Drift

## Overview
No frozen frames: a tripod stock clip drifts a few percent along the same eased curve as Ken Burns.
(The S-curve Ken Burns itself shipped with phase 1.)

## Requirements
- `focus.analyse()` flags a video `still` when its mean frame difference < `STILL_MOTION`.
- `clips._stream()` for a still video: scale by `DRIFT_SCALE` (1.06), crop window travels `DRIFT` (4 %) of the
  frame width (half that vertically) along `_ease("n", t0, span)`, direction alternating per shot, clamped
  to the frame; continuous across the cross-fade (same t0/span as Ken Burns). Moving clips unchanged.

## Results (2026-09-30)
- Threshold calibrated on 102 real stock clips: percentiles 10/20/30/50 = 0.023/0.031/0.037/0.057; every clip
  ≤ 0.031 inspected was a tripod shot (talking head, desk close-up, night sky) → `STILL_MOTION = 0.03`.
- Test pattern: travel follows (1-cos πx)/2 within 1 px (frame 45: -21.9 px vs -21.6 expected; end -43.9 vs -43.2).
- Black-hole short re-render: 6 of the shots drifted (star-trail tree ~40 px over 2 s), render 63 s,
  -14.2 LUFS; 362 tests green.
- Limit: `crop` moves in whole pixels (~0.5 px/frame average), so the slowest part of the ease steps by 1 px;
  not visible at 1080 px in the checked frames.
