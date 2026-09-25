---
phase: 7
title: "End-to-End Testing"
status: pending
priority: P1
effort: "1d"
dependencies: [6]
---

# Phase 7: End-to-End Testing

## Overview
Verify all 4 combos (short/long × vi/en) on the user's GPU against MVP acceptance criteria; write usage README.

## Requirements
- Functional: 4 real runs produce publishable MP4s.
- Non-functional: timing budgets met; $0 API spend.

## Implementation Steps
1. Unit test suite green (`pytest`).
2. Offline smoke test: fake LLM + fake TTS + color-clip visuals → render path works without network (`tests/test_smoke.py`, marked `slow`).
3. Real runs:
   - short vi: "Bí ẩn tam giác Bermuda"
   - short en: "Why octopuses are basically aliens"
   - long vi: "Lịch sử con đường tơ lụa"
   - long en: "The lost city of Atlantis: myth vs evidence"
4. Checklist per run: ffprobe resolution/fps/duration; watch full video for caption drift, wrong/irrelevant visuals, audio levels, diacritics; check `metadata.json`.
5. Record elapsed times per stage; tune worker counts / AI caps if over budget.
6. Write `README.md`: setup (uv, FFmpeg, ComfyUI models, API keys), commands, review workflow, platform AI-label reminder, music licensing note.

## Success Criteria
- [ ] 4/4 combos render successfully
- [ ] Short <10 min, long <45 min end-to-end
- [ ] Caption drift ≤0.2s, no watermarks, credits present
- [ ] README lets a fresh setup run `make` successfully

## Risk Assessment
- Stock relevance subjective → log per-scene query + chosen asset to tune prompts later.
