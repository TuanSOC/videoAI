---
phase: 5
title: "Vision Judge"
status: pending
priority: P1
effort: "4h"
dependencies: [4]
---

# Phase 5: Vision Judge

## Overview
qwen2.5vl:7b (Ollama) looks at thumbnails of the top 5 text-ranked candidates per scene and scores them;
the selector picks by vision score. Replaces the text-only `llm_judge` for stock picks.

## Requirements
- Functional:
  - `vision.py`: `VisionJudge.score(narration, queries, images: list[bytes]) -> list[int] | None` —
    one Ollama `/api/chat` call, `images` base64, `format` JSON schema `{scores: [int 0-10]}`, prompt
    criteria: shows what the narration talks about; looks real/unstaged; no text/logo/green screen.
  - Selector: top VISION_TOP=5 with thumbs → download thumbs (httpx, small, cached in cache/thumbs) →
    score → sort by (score, text rank). Drop < MIN_VISION=5; if none ≥5 keep best (never placeholder
    because of vision). Images penalised −1 vs video.
  - Vision failure (Ollama down, model missing, invalid JSON, timeout) → None → current text ranking.
    Job never fails because of vision. Log once per video if model missing ("ollama pull qwen2.5vl:7b").
  - VRAM: before the visuals stage, unload qwen3 (`/api/generate` with `keep_alive: 0`); vision calls use
    `keep_alive: "2m"`; after visuals, unload vision model. Settings: `vision.model`, `vision.enabled`.
  - Two-pass sourcing so the model loads once: pass 1 search+score all scenes (sequential vision calls),
    pass 2 background downloads (existing pool).
  - Swap endpoint uses the same judge when enabled.
  - Store `Asset.vision_score` for UI display (optional badge; minimal: expose in API asset view).
- Non-functional: ≤ 4 min added for a 12-scene short on RTX 4060 8 GB; timeout 60 s/call.

## Related Code Files
- Create: `src/vidgen/visuals/vision.py`
- Modify: `src/vidgen/visuals/selector.py`, `src/vidgen/pipeline.py` (build_selector wiring, unload),
  `src/vidgen/script/llm.py` (unload helper), `src/vidgen/config.py`, `config.yaml`,
  `src/vidgen/web/app.py` (asset view score), `src/vidgen/web/static/app.js` (score badge, optional)
- Tests: `tests/test_visuals.py`, new `tests/test_vision.py`

## Implementation Steps (TDD)
1. Tests first (httpx.MockTransport / fake judge, no live Ollama):
   - request body has `images` (base64) count = thumbs, model name, `format` schema.
   - parse `{scores:[8,2,...]}`; wrong length / invalid JSON → None.
   - selector with fake judge: judge prefers 3rd candidate → picked; all < 5 → best kept; judge None →
     text order kept; image penalty applied.
   - candidates without thumb skipped for vision but still rankable.
   - unload helper posts keep_alive 0.
   - pin: existing selector tests (judge disabled by default in tests).
2. Implement vision.py → selector integration → pipeline wiring → config.
3. `ollama pull qwen2.5vl:7b` (≈6 GB; confirm with user before pulling — large download).
4. Full suite; live timing on phishing in phase 7.

## Success Criteria
- [ ] Tests pass; live: scenes 1/6/7/8 of phishing get relevant clips; timing within budget.

## Risk Assessment
- 8 GB VRAM: vision 7b q4 ~6 GB + image tokens; if OOM/slow → fall back to `qwen2.5vl:3b` via config.
- Thumbnails are one frame; a clip may change content later → acceptable; focus.py still samples frames.
- Model download is large: explicit user confirmation before pull.
