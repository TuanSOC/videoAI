---
phase: 4
title: Live verification
status: completed
priority: P1
effort: 0.25d
dependencies:
  - 1
  - 2
  - 3
---

# Phase 4: Live verification

## Steps
1. `vidgen series … --until 2026-10-09`, interrupt mid-run (kill the process) → rerun resumes the same folder.
2. Start the studio during a series run → the video shows running; render/resume/look → 409; after the run the
   studio can work it.
3. Full suite green; one fresh short end to end (voice → visuals with ComfyUI → render) with frame check.

## Results (2026-09-30)
- `vidgen series` started as its own process; during its voice stage the studio showed "series running voice"
  and refused render / resume / look with 409 "…being worked on by another process (series pid=24100 since=21:17:12)".
- Hard kill (taskkill /T /F) mid-voice: lock freed by the OS at once; the studio showed the job interrupted
  (before: "running" forever, every later run refused).
- Rerun: resumed the same folder (voice finished the missing part in 17 s), visuals 149 s, render 73 s;
  66.6 s, yuv420p/tv, -14.0 LUFS, cyber_tech look, 3 fact-check flags for review; edge-tts throttled once and
  the ~1 min retry got through. The only freezedetect hit is a slow stock clip in the last 1.3 s (frames still
  change 0.7-2.0 mean), not a stall.
- Suite: 451 green.
