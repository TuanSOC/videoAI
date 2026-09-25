---
phase: 3
title: "Brief Step and Multi-Topic"
status: pending
priority: P1
effort: "1d"
dependencies: [1, 2]
---

# Phase 3: Brief Step and Multi-Topic

## Overview
Rough ideas → (split) → one video per topic → 3 angles with sources → user picks/edits one → grounded script.

## Requirements
- Functional:
  - `POST /api/ideas {text, format, lang, auto_render}` → list of created videos (max 5). Single short line (<120 chars, no newline/bullet) skips the split LLM call.
  - Brief job per video → `brief.json` with 3 angles of different styles (explain / myth-bust / story), each: title, hook, 3-4 key points, query_en, query_local, keywords, sources (with text).
  - `POST /api/videos/{slug}/brief {angle, title?, hook?, key_points?, excluded_urls[]}` → saves choice → script job.
  - Script writer follows the chosen angle (title, opening hook, key points in order) and only non-excluded sources.
  - auto_render: brief job picks angle 0 → script → render.
  - Legacy videos (no brief.json) keep working (script job researches from topic as today).
- Non-functional: brief ≈15-25s/topic on qwen3:8b; lookups cached within a job.

## Architecture
Models (`models.py`):
```python
class SourceDoc(BaseModel): title; url; lang; text
class Angle(BaseModel): style; title; hook; key_points: list[str]; query_en; query_local; keywords: list[str]; sources: list[SourceDoc] = []
class Brief(BaseModel): topic; angles: list[Angle]; chosen: int | None = None; excluded_urls: list[str] = []
```
New `script/brief.py`: `split_ideas(text, lang, llm) -> list[str]`, `make_brief(topic, lang, llm, wiki) -> Brief` (1 LLM call for angles, then `research` per angle with angle's queries/keywords, cache by (lang, query)).
Pipeline: `init_video` unchanged; new `write_brief(out_dir, s)`; `write_script` reads brief.json when present → `generate(..., angle=chosen, sources=filtered)`; else legacy research path.
Writer: `generate(..., angle: Angle | None)` injects `$angle` block into all prompts ("Title/Hook/Cover these points in order"); title := angle.title when given.
Status: brief.json exists & no script.json → `brief`; pill "Chọn góc".
CLI `make`: brief → auto-pick `--angle` (default 1) → print angles → continue as before.
UI:
- Library: textarea "Ý tưởng video — mỗi dòng một ý", button "Phân tích ý tưởng" (spinner during split); 1 video → navigate, N → toast + stay.
- Detail `brief` status: 3 editable angle cards (style badge, title input, hook textarea, key points textarea one-per-line, sources with checkbox + link) + "Chọn góc này & viết kịch bản". Running brief → skeleton "Đang tìm góc & nguồn…". Script view gets "Chọn góc khác" (back to cards).

## Related Code Files
- Create: `src/vidgen/script/brief.py`, `src/vidgen/script/prompts/brief_angles.md`, `src/vidgen/script/prompts/split_ideas.md`, `tests/test_brief.py`
- Modify: `src/vidgen/models.py`, `src/vidgen/script/writer.py`, `src/vidgen/script/prompts/{short,long_outline,long_chapter}.md`, `src/vidgen/pipeline.py`, `src/vidgen/cli.py`, `src/vidgen/web/app.py`, `src/vidgen/web/static/{app.js,app.css}`, `tests/test_web.py`, `tests/test_pipeline.py`

## Implementation Steps
1. Models + brief.py (split, angles, per-angle research with cache).
2. Writer/prompt `$angle` injection; title override.
3. Pipeline `write_brief`, brief-aware `write_script`, sources.md from chosen sources.
4. API: `/api/ideas`, `/brief` choose endpoint, brief job kind, status `brief`, auto_render path.
5. UI: ideas form, angle cards, "Chọn góc khác".
6. CLI `make --angle`.
7. Tests (fake LLM/wiki): split 2 topics; single-line skip; 3 angles saved; choose → script uses angle + excluded sources removed; auto_render picks angle 0; legacy video path.
8. Live: 2-topic paste; "bạch tuộc".

## Success Criteria
- [ ] 2-topic paste → 2 videos with correct sources
- [ ] "bạch tuộc" → 3 distinct angles
- [ ] Chosen angle's title/hook appear in script; unticked source not in prompt
- [ ] Tests pass

## Risk Assessment
- Similar angles from 8B model → prompt enforces 3 named styles.
- Split too eager (one idea split in two) → user deletes extra; cap 5.
- Brief JSON larger (sources text) → stored in brief.json, not sent in list API.
