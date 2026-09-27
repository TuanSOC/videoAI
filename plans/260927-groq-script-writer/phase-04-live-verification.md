---
phase: 4
title: Live verification
status: completed
priority: P2
effort: 1h
dependencies:
  - 1
  - 2
  - 3
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

## Results (2026-09-27)
- ✨ "wannacry" → "WannaCry: vì sao một lỗ hổng đã vá vẫn gây hỗn loạn toàn cầu?" (gpt-oss-120b, ~3 s); [Dùng] fills
  the box; brief 8.2 s with specific angles (kill-switch / Marcus Hutchins story).
- Story angle, full render: script 5.1 s on Groq (qwen3:8b took minutes), 12 scenes / 58 s, -14.2 LUFS, no
  fallback; facts: kill-switch domain, Hutchins sinkhole, 14-15/5 variants, Check Point, Matt Suiche.
- Found live and fixed: short drafts → strong prompt stresses length; expansions came back in English → prompt +
  code guard; U+2011 hyphens → normalised; domain read aloud → prompt rule; second comment CTA mid-script → code
  guard; qwen3 checker listed "Đúng." notes as flags → filtered. Final fact-check: 2 flags (hook overclaims
  "ngăn chặn" vs "làm chậm"; a date-format nitpick).
- Fallback verified: invalid key → both Groq models fail fast (401) → ollama:qwen3:8b, usage fallback=True.
- Not done: long 16:9 smoke; doc cards don't match dates written "12/5/2017" (figure = all digits).
