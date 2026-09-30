---
phase: 3
title: Script and series fixes
status: completed
priority: P2
effort: 0.25d
dependencies: []
---

# Phase 3: Script and series fixes

## Requirements
- Finding 5: check_facts reads script.json inside the try (bad script → checked=False, no raise).
- Finding 6: strip_meta removes only (visual) metaphor(ical)(of)/symbolic; empty result → alt query /
  previous scene's query (like a non-ASCII query).
- Finding 7: rewrite_one applies strip_meta to visual_query + alt_queries and plain() to ai_prompt.
- Finding 10: `series.status(frame, state, today, done)` yields (date, ep, lang, status, slug) — used by the CLI
  table and run(); `due()` removed; redundant LOOKS import removed.
