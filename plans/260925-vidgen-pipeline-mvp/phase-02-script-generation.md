---
phase: 2
title: Script Generation
status: in-progress
priority: P1
effort: 1d
dependencies:
  - 1
---

# Phase 2: Script Generation

## Overview
Topic → validated `script.json` (scenes with narration + English visual queries) via Gemini free tier, Ollama fallback.

## Requirements
- Functional: short (45-75s, hook in first sentence, ~120-180 words) and long (8-15 min, ~1200-2200 words, chapters) in vi/en.
- Non-functional: JSON output validated by Pydantic; retry up to 2× on invalid JSON; provider fallback on 429/5xx.

## Architecture
```
script/
  llm.py         # LLMProvider protocol: generate_json(prompt, schema) -> dict
                 # GeminiProvider (google-genai, response_schema), OllamaProvider (format=json)
  prompts/short.md  prompts/long.md   # templates with {topic} {lang} {target_words}
  writer.py      # build prompt → call provider chain → validate → post-process
```
Post-process rules:
- `visual_query` always English (stock APIs work best in English), 2-4 concrete nouns ("stormy ocean aerial").
- Split scenes whose narration >25 words (≈8-10s) so visuals change often.
- LLM marks `visual_type=ai_video` only when stock is unlikely (fantasy/historical recreation); writer enforces `max_ai_video` cap by downgrading extras to `ai_image`.
- Long format: generate outline (chapters) first, then scenes per chapter (avoids truncated output, keeps coherence).

Prompt guidance: "fascinating facts / storytelling" tone, no fabricated statistics, open with curiosity hook, end with CTA question for comments.

## Related Code Files
- Create: `src/vidgen/script/{__init__,llm,writer}.py`, `src/vidgen/script/prompts/{short,long}.md`, `tests/test_script_writer.py`

## Implementation Steps
1. Implement provider protocol + Gemini (model from config, e.g. `gemini-flash-latest`) + Ollama.
2. Write prompt templates (one per format, lang injected as variable — DRY).
3. `writer.generate(topic, fmt, lang) -> Script`: chain providers, validate, post-process.
4. Long: two-step outline → per-chapter scenes.
5. Save `output/<slug>/script.json`.
6. Unit tests with fake provider: scene splitting, AI cap enforcement, invalid JSON retry.

## Success Criteria
- [ ] Short vi/en script within word target
- [ ] Long script has chapters, total words in range
- [ ] Gemini failure → Ollama used automatically
- [ ] Tests pass

## Risk Assessment
- Gemini free tier limits change → Ollama fallback (Qwen 2.5/3 7B-14B fits 12GB).
- Vietnamese quality of local model weaker → Gemini primary for vi.
