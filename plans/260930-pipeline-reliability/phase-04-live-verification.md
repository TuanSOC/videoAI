---
phase: 4
title: "Live verification"
status: pending
priority: P1
effort: "0.25d"
dependencies: [1, 2, 3]
---

# Phase 4: Live verification

## Steps
1. `vidgen series … --until 2026-10-09`, interrupt mid-run (kill the process) → rerun resumes the same folder.
2. Start the studio during a series run → the video shows running; render/resume/look → 409; after the run the
   studio can work it.
3. Full suite green; one fresh short end to end (voice → visuals with ComfyUI → render) with frame check.
