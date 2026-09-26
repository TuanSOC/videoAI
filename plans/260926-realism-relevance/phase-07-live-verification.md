---
phase: 7
title: Live Verification
status: completed
priority: P1
effort: 1.5h
dependencies:
  - 1
  - 2
  - 3
  - 4
  - 5
  - 6
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

## Results (2026-09-26, phishing short, vi)
| Criterion | Before | After (run -3) |
|---|---|---|
| Inter-scene silence | 1.0–1.4 s | max 0.33 s ✅ |
| Subtitles | 1–3 words, pop/zoom | 48 events, all ≤26 chars, 3–6 words (or short sentence) ✅; 1 unavoidable function-word end in a 12-word sentence |
| Clips | green screen, bare palm, cash, calculator | 10/12 vision-scored (one 10/10, four 8/10, rest 5); no junk; 2 weak (calculator phone 5, letter board image unscored) ⚠️ |
| Pipeline time | 105 s | 235 s (+130 s, budget +240 s) ✅ |
| Length | 57 s | 52 s after WPS recalibration (run -2 was 33 s at old WPS) ✅ |
| Music | none | mood "tense" tagged; plays once tracks are added to assets/music/tense/ |

Live-run fixes: adjectives excluded from subject, no unjudged strict picks after the judge answered,
MIN_VISION 6 / MIN_FALLBACK 3 / 3 calls, quote-aware sentence ends, WPS vi 3.9 (en 3.0 unmeasured).
Remaining: image steps can run out of judge calls (seen: letter-board image); acceptable, user can swap.

### After code review + perf fixes (run -7, final)
- Review fixes: rejected clips excluded per scene, weak clip deferred until image/AI steps fail, text judge kept
  as backup, bad thumbnails not cached, comma query fix, judge gives up after 2 failures, lazy unload,
  voice_rate validation, pyvi fallback, web tests isolated from Ollama.
- Live regressions found and fixed: (1) Ollama evicts asynchronously → vision model loaded half on CPU,
  120 s timeouts → unload now waits for /api/ps; (2) Ollama upscales small images (~1.1k tokens each) →
  candidates sent as ONE numbered strip (~1.2k tokens total, num_ctx 4096, fully in VRAM, 0.6–2 s/call,
  scored better than separate images); (3) pass mark 5 (prompt defines 5 as acceptable).
- Final: total 203 s (baseline 105 s, +98 s ✅), visuals 79 s, scores [5,5,5,8,5,10,5,8,8], no junk clips,
  gaps ≤0.33 s, captions within limits.
- Known limits: 7B judge separates junk (0) from usable (5) well but ranks usable clips coarsely; script
  length varies with the LLM (this run 9 scenes / 33 s; previous 52 s); en words-per-second unmeasured.
