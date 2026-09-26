---
phase: 4
title: Queries And Thumbnails
status: in-progress
priority: P1
effort: 3h
dependencies: []
---

# Phase 4: Queries And Thumbnails

## Overview
Each scene gets 3 concrete, filmable English queries; candidates carry a thumbnail URL; obvious junk
(green screen, chroma key, mockup, template) is filtered; lossy fallback shortening no longer produces
stopword fragments ("email with").

## Requirements
- Functional:
  - `Scene.visual_queries: list[str] = []` (max 3). `visual_query` stays = first (backward compatible;
    old script.json without the list still works → list derived as [visual_query]).
  - Prompts (short.md, long_chapter.md, rewrite_scene.md, expand.md) ask for `visual_queries` of 3 distinct
    shots: literal action, close-up object, setting. English guard applies to all three.
  - `Candidate.thumb: str` — Pexels video `image`, Pexels photo `src.medium`, Pixabay video
    `videos.tiny.thumbnail` / photo `webformatURL`. `Alternate.thumb` too.
  - JUNK words in candidate text → excluded before ranking.
  - `fallback_queries`: never emit a query whose last token is a stopword; skip fragments < 2 content words
    unless it is the subject word.
  - Selector searches all queries of the scene, pools candidates (dedupe by uid), ranks as now.
- Non-functional: search calls ≤ 3× previous per scene (cache in stock clients already helps).

## Related Code Files
- Modify: `src/vidgen/models.py`, `src/vidgen/script/writer.py` (postprocess: fill visual_query from list),
  prompts/*.md, `src/vidgen/visuals/stock.py`, `src/vidgen/visuals/selector.py`, `src/vidgen/web/static/app.js`
  (scene editor shows/edit first query only — no UI change needed if visual_query kept)
- Tests: `tests/test_script_writer.py`, `tests/test_visuals.py`

## Implementation Steps (TDD)
1. Tests first:
   - Scene without `visual_queries` → selector uses [visual_query].
   - postprocess: `visual_queries` non-English entry dropped; visual_query = first survivor.
   - Pexels parse fills `thumb`; Pixabay parse fills `thumb`.
   - candidate text "a computer monitor with a green screen on it" is excluded.
   - `fallback_queries("email with suspicious attachment")` never returns "email with".
   - pool: two queries returning overlapping candidates → no duplicate uid, both queries' hits considered.
   - pin: existing selector/swap/alternates tests.
2. Implement models → stock → selector → writer/prompts.
3. Full suite.

## Success Criteria
- [ ] Tests pass; phishing re-script shows 3 queries/scene in script.json.

## Risk Assessment
- qwen3 may return near-duplicate queries → postprocess dedupes by content-word set.
- Old assets.json Alternates without thumb → default "" (vision skips them, text rank used).
