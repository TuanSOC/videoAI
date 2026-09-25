---
phase: 4
title: "Length Indicator"
status: pending
priority: P2
effort: "0.25d"
dependencies: [3]
---

# Phase 4: Length Indicator

## Overview
Script editor shows estimated duration vs the format's target range, and the real duration once the voice exists.

## Requirements
- Functional: bar with target range markers; green inside, amber outside; live update while editing; "thực tế: 0:52" when timeline.json matches current script.
- Non-functional: no extra API calls while typing.

## Architecture
- `GET /api/videos/{slug}` adds `target_seconds: [lo, hi]`, `wps` (from writer.WORDS_PER_SECOND), `actual_seconds` (timeline duration if `state.script_hash == hash(script.json)`, else null).
- UI computes estimate = words / wps; bar scale 0..hi×1.25.

## Related Code Files
- Modify: `src/vidgen/web/app.py`, `src/vidgen/web/static/{app.js,app.css}`, `tests/test_web.py`

## Implementation Steps
1. API fields.
2. UI bar component in scenes header; recompute on narration input.
3. Test: API returns target range; actual_seconds null after script edit.

## Success Criteria
- [ ] Amber when <45s or >75s (short), green inside
- [ ] Actual duration shown after voice, hidden after edits
- [ ] Tests pass

## Risk Assessment
- WPS estimate differs from real voice → actual value shown once available.
