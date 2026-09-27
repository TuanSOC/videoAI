---
phase: 3
title: Topic enhance and model badge
status: completed
priority: P2
effort: 3h
dependencies:
  - 1
  - 2
---

# Phase 3: Topic enhance and model badge

## Overview
✨ button turns a short topic into a specific, hooky one (user approves); studio shows which model wrote the
brief/script and warns when it fell back to local Ollama.

## Requirements
- `script/enhance.py`: `enhance_topic(topic, lang, fmt, llm) -> TopicIdea(topic, reason)`; prompt
  `prompts/enhance_topic.md` (+ strong variant optional): one specific topic ≤ 20 words in `$lang_name`,
  concrete subject + angle/question; NO figures, dates or claims not in the user's words (it is not
  researched yet); `reason` one short sentence. Empty/over-long output → 422.
- `POST /api/topic/enhance {topic, lang, format}` → `{topic, reason, model}`; 503 with message if all
  providers fail. Not a job (one call, a few seconds on Groq).
- UI (`web/static/app.js` + html/css): ✨ button beside the topic input (disabled while empty/pending);
  suggestion box under the input with [Dùng] (replaces input) / [Bỏ qua]; errors shown inline.
- Model record: brief/script/rewrite/expand stages store `state["llm"][<stage>] = {"model", "fallback"}`
  from `chain.last_provider/fallback`; `get_video` returns it; brief/script panels show
  "Viết bởi gpt-oss-120b" and a yellow note "Groq không dùng được, đã viết bằng qwen3:8b (local)" on fallback.

## Related Code Files
- Create: `src/vidgen/script/enhance.py`, `src/vidgen/script/prompts/enhance_topic.md`, tests
  `tests/test_enhance.py`
- Modify: `src/vidgen/web/app.py`, `src/vidgen/web/static/app.js` (+ index.html/css), `src/vidgen/pipeline.py`

## Implementation Steps (TDD)
1. Tests: enhance with fake chain → TopicIdea; endpoint 200 shape; all-fail → 503; state records model +
   fallback after script stage with fake chain; `get_video` exposes it.
2. Implement backend, then UI; check in browser (topic input → ✨ → Dùng → create brief).

## Success Criteria
- [ ] "wannacry" → a specific Vietnamese topic without invented figures; accept fills the input.
- [ ] Script panel shows the model; fallback warning visible when Groq key removed.

## Risk Assessment
- Enhanced topic drifting from user intent → always a suggestion, never auto-applied.
