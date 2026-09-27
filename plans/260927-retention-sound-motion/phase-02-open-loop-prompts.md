---
phase: 2
title: Open Loop Prompts
status: completed
priority: P2
effort: 3h
dependencies: []
---

# Phase 2: Open Loop Prompts

## Overview
Short scripts with a hard 3 s hook (paradox / mystery / warning) and an open loop: a question raised
by ~5 s and answered near the end.

## Requirements
- Functional:
  - Structure: scene 1 hook (paradox/mystery/warning, ≤ ~10 words) → scene 2 poses the open question →
    body → **payoff in the second-to-last scene** → very short CTA question last (resolves the existing
    "last scene = comment question" rule).
  - ShortDraft gains `open_loop: str` and `payoff_scene: int`; validated (payoff ≥ n−2).
  - Banned generic openers (vi/en list: "Bạn có biết", "Hôm nay chúng ta", "Xin chào", "In this video",
    "Did you know"…) → one hook rewrite request; still bad → keep and log.
  - brief_angles.md: angle hooks follow the same rules; "myth" angle framed as paradox.
- Non-functional: no extra LLM call in the happy path.

## Related Code Files
- Modify: `src/vidgen/script/prompts/short.md`, `brief_angles.md`, `src/vidgen/script/writer.py`,
  `src/vidgen/script/brief.py`
- Tests: `tests/test_script_writer.py`, `tests/test_brief.py`

## Implementation Steps (TDD)
1. Tests: banned opener triggers rewrite (fake LLM); payoff index validation; prompt contains structure
   rules; brief hooks filtered.
2. Implement prompt + checks.
3. Live: 3 topics, check hook word count and payoff placement.

## Success Criteria
- [ ] No generic opener in 3 live scripts; open question in scene 2; payoff before the CTA.

## Risk Assessment
- 8B model may ignore structure → validation + single retry; honest: retention >60 % can't be
  guaranteed by prompts.

## Results (2026-09-27)
- 285 tests green (tests/test_hooks.py: 15 new).
- Live, 3 vi topics (Bermuda, honey, home cameras), final run: all 3 follow hook → open question (scene 2)
  → answer (second-to-last) → short CTA; no generic opener; numbers as digits.
- What it took (the 8B model ignored the prompt alone): generic openers cut in code (no LLM call);
  angle hook inserted/replaces a paraphrase; `open_loop`/`payoff_scene` made REQUIRED in the schema
  (optional → the model omitted them); the answer scene moved before the CTA; expansion never inserts
  after the answer.
- Limits: a long descriptive hook the rewrite can't shorten is kept (Bermuda: 13 words, factual); the
  model sometimes labels a question as the "answer". Retention itself can't be measured here.
