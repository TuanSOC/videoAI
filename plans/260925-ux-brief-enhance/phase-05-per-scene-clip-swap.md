---
phase: 5
title: "Per-Scene Clip Swap"
status: pending
priority: P2
effort: "0.75d"
dependencies: [1]
---

# Phase 5: Per-Scene Clip Swap

## Overview
After render, replace one scene's clip (next alternate or a new query) and re-render only the final video; other scenes untouched, no LLM call.

## Requirements
- Functional:
  - Each scene card shows a thumbnail of its current visual + "Đổi clip" (optional query input).
  - `POST /api/videos/{slug}/scenes/{id}/swap {query?}` → next unused alternate, else search (query or fallback queries) excluding every uid used in the video.
  - Swaps mark video "needs re-render"; one "Dựng lại video" runs render only.
  - Metadata credits refreshed without LLM.
- Non-functional: short re-render <30s.

## Architecture
- `Asset` gains `uid: str = ""`, `query: str = ""`, `alternates: list[Alternate] = []` (Alternate = Candidate fields, ≤3, ranked).
- Selector records alternates when choosing stock; exposes `swap(asset, query, used_uids) -> Asset`.
- `Metadata` gains `summary` (LLM description) so `rebuild_description(meta, script, assets)` can regenerate credits.
- Swap endpoint: busy → 409; update assets.json atomically; remove old visual file; clear ONLY the render stage's artifacts (new `invalidate_stage(out_dir, "render")` — `invalidate_from` would also delete metadata.json). After a render, if metadata.json already exists, `_render` calls `rebuild_description` to refresh credits (no LLM).
- `GET /media/{slug}/scenes/{id}.jpg` thumbnail (ffmpeg frame 0.5s / image scale), cached by asset mtime.

## Related Code Files
- Modify: `src/vidgen/models.py`, `src/vidgen/visuals/selector.py`, `src/vidgen/metadata.py`, `src/vidgen/pipeline.py`, `src/vidgen/web/app.py`, `src/vidgen/web/static/{app.js,app.css}`
- Modify: `tests/test_visuals.py`, `tests/test_web.py`

## Implementation Steps
1. Model fields; selector alternates + `swap()`.
2. Metadata summary + rebuild; render path refreshes metadata credits when metadata exists.
3. Swap + thumbnail endpoints.
4. UI thumbnails, swap popover (query optional), "Đã đổi N clip — Dựng lại video".
5. Tests: alternates recorded; swap picks next unused; query swap searches; no duplicate uids; metadata credits updated w/o LLM.
6. Live: swap scene 3 of a short, time re-render.

## Success Criteria
- [ ] Swapped clip new & unique; others unchanged (byte-identical segment inputs)
- [ ] Re-render <30s for short; metadata credits correct
- [ ] Tests pass

## Risk Assessment
- Old videos lack alternates → swap falls back to search.
- Placeholder/AI scenes → swap searches stock.
