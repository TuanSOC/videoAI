---
phase: 4
title: "Live verification"
status: pending
priority: P2
effort: "1h"
dependencies: [1, 2, 3]
---

# Phase 4: Live verification

## Overview
Real run through the studio with the Groq key; compare against the qwen3:8b WannaCry script.

## Implementation Steps
1. Put `GROQ_API_KEY` in `.env`; restart server.
2. ✨ enhance "wannacry" (vi, short) → accept → brief → angle 0 → render.
3. Record: model used per stage, 429 waits (server log), scene count, distinct facts/figures, fact-check flags,
   doc cards, total time; compare with the previous qwen3:8b script (17 scenes, 3 flags).
4. Fallback check: invalid key → still produces a script via Ollama, warning shown.
5. Long 16:9 smoke (optional, if TPD budget allows).

## Success Criteria
- [ ] Groq script: ≥4 concrete sourced facts, flags ≤ baseline, hook/open-loop/payoff/CTA present.
- [ ] End-to-end render OK (LUFS ≈ -14, captions/cards fine).
- [ ] Fallback path verified.

## Risk Assessment
- Daily token cap (TPD) could stop repeated live tests → keep runs few; fallback covers.
