---
phase: 2
title: Subtitle Chunks
status: completed
priority: P1
effort: 2h
dependencies: []
---

# Phase 2: Subtitle Chunks

## Overview
Short format: one line, 3–6 words, ≤26 chars, white + black outline, spoken word turns yellow instantly
(`\k`, not `\kf`). No POP, no emphasis scaling. Long format unchanged.

## Requirements
- Functional: chunks break on punctuation/pauses; target 3–6 words and ≤ SHORT_MAX_CHARS=26; never end on
  FUNCTION_WORDS, never split number+unit, no 1-word orphan before sentence end (existing rules kept).
  A sentence shorter than 3 words is one chunk.
- Safe-zone margins (110/210/620) unchanged. Font size may drop from 90 → ~78 to fit 26 chars at 1080 px
  minus margins (verify width: 26 chars × ~0.55em).

## Architecture
`subtitles.py`: `chunk_words` gains `max_chars` and `min_words`: breaking at `over` requires
`len(current) >= min_words`; `over` = words ≥ max_words OR chars of next word would exceed max_chars.
`_events_short`: `{\kNN}` tags, no POP, no EMPHASIS wrap. Remove `emphasis_words` use in short path
(keep function — long format still highlights). Style primary YELLOW / secondary WHITE stays (\k switches
secondary→primary at word start).

## Related Code Files
- Modify: `src/vidgen/assemble/subtitles.py`
- Tests: `tests/test_assemble.py` (update karaoke/pop/emphasis tests for short)

## Implementation Steps (TDD)
1. Tests first:
   - phishing-like sentence "Hacker tạo email giả mạo để gửi link hoặc file đính kèm chứa mã độc." →
     every chunk 3–6 words, ≤26 chars, none ends on "để"/"hoặc"/"chứa".
   - 2-word sentence "Cẩn thận." → single chunk.
   - short ASS: contains `{\k`, not `\kf`, not POP, not EMPHASIS_ON.
   - pin: long-format tests unchanged; number+unit test; function-word test.
2. Implement; adjust `STYLES["short"]` font size after width check.
3. Full suite.

## Success Criteria
- [ ] All chunk constraints hold in tests and on phishing re-render (phase 7 script check over subs.ass).

## Risk Assessment
- Char limit vs Vietnamese diacritics width: measure one rendered frame in phase 7; tune font size.
