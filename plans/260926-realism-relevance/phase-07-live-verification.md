---
phase: 7
title: "Live Verification"
status: pending
priority: P1
effort: "1.5h"
dependencies: [1, 2, 3, 4, 5, 6]
---

# Phase 7: Live Verification

## Overview
Re-make the phishing video end-to-end and measure every acceptance criterion; re-render bach-tuoc as a
regression check; commit; restart server only when `/api/videos` shows no running job.

## Implementation Steps
1. `ollama pull qwen2.5vl:7b` done (phase 5, user-confirmed).
2. One-click create: same phishing topic, short, vi. Record wall time per stage (job log).
3. Measure:
   - gaps: from timeline.json, next.first_word − prev.last_word (global) ≤ 0.4 s.
   - subs: parse subs.ass → words per event 3–6 (or whole short sentence), visible chars ≤ 26, last token
     not in FUNCTION_WORDS.
   - clips: contact sheet (fps 1/2.5 tile) + assets.json vision scores; scenes about clicking link / fake
     bank / checking email show matching content.
   - transitions: frames around a scene boundary show ~5-frame dissolve, cuts inside scenes.
   - time: total added vs previous 105 s baseline ≤ +4 min.
4. Re-render bach-tuoc (render stage only) → no errors, render time ≤ previous.
5. Code review subagent on the diff; fix findings.
6. Commit per phase or one commit; idle-check; restart server.

## Success Criteria
- [ ] All plan-level acceptance items measured and passing, numbers reported to user.

## Risk Assessment
- Pexels content varies per run; judge relevance by vision scores + manual contact-sheet review, not by
  exact clip ids.
