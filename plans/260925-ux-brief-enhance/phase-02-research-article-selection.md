---
phase: 2
title: Research Article Selection
status: completed
priority: P1
effort: 0.25d
dependencies: []
---

# Phase 2: Research Article Selection

## Overview
Replace blind top-1 Wikipedia hit with top-5 candidates judged by the LLM, which may reject all.

## Requirements
- Functional: search `srlimit=5` with snippets; LLM picks the article that factually covers the topic, rejecting fiction/folklore/songs/people/unrelated species/disambiguation; "none" → no source for that language.
- Non-functional: +1 LLM call (~3s) per lookup; lookups cached per job by `(lang, query)`.

## Architecture
```python
class ArticlePick(BaseModel):
    index: int  # 0-based candidate, -1 = none relevant
    reason: str

Wikipedia.search(lang, query, limit=5) -> list[Hit(title, snippet)]  # strip <span> tags
pick_article(topic, hits, llm) -> Hit | None
Wikipedia.lookup(lang, query, keywords, topic, llm) -> Source | None
```
Prompt: topic + numbered candidates (title — snippet); "Pick the encyclopedic article about the real-world subject the video explains. Reject fairy tales, novels, films, songs, people, other species with similar names, disambiguation pages. Answer -1 if none fits."

## Related Code Files
- Modify: `src/vidgen/script/research.py`
- Modify: `tests/test_research.py`

## Implementation Steps
1. `search()` returning hits with cleaned snippets; `pick_article()`; wire into `lookup`/`research`.
2. Out-of-range index → treat as none.
3. Tests: fairy-tale-first candidate list → picks "Seawater"; all irrelevant → none; bad index → none.
4. Live check with "Vì sao nước biển mặn" → Seawater / Nước biển (or similar), not "Why the Sea is Salt".

## Success Criteria
- [ ] Live: salty-sea topic grounded on a seawater article, not folklore
- [ ] Live: octopus still finds Octopus/Bạch tuộc
- [ ] Tests pass

## Risk Assessment
- LLM may still pick wrong → user sees/unticks sources in Brief (phase 3).
