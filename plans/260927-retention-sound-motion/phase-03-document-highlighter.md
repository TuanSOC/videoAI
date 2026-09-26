---
phase: 3
title: "Document Highlighter"
status: pending
priority: P3
effort: "5h"
dependencies: []
---

# Phase 3: Document Highlighter

## Overview
For evidence/statistic scenes, render a document-style card (paper texture, sourced quote) with a
highlighter sweeping over the key phrase, instead of a stock clip.

## Requirements
- Functional:
  - Scene selection: LLM marks `visual_type: "document"` with `quote` + `highlight` (substring), max 2
    per short; or scenes whose narration contains a number and a source exists.
  - Render card with Pillow (Be Vietnam Pro; OpenCV can't draw Vietnamese diacritics): paper tone,
    quote text wrapped, source line "Nguồn: <Wikipedia title>".
  - Animation in FFmpeg: slow push-in + `drawbox` highlight whose width grows with `t` over the phrase
    bbox (bbox measured by Pillow), yellow/red at ~40 % opacity; the existing grade/grain applies.
  - Shot kind `document` in plan_shots; no cross-fade issues (acts like an image).
- Ethics / platform risk: never imitate a real publication (no mastheads, logos, bylines, fake dates);
  only real sourced text; source named on the card.

## Related Code Files
- Create: `src/vidgen/assemble/document.py`, `tests/test_document.py`
- Modify: models (`Scene.quote`, `highlight`, visual type), prompts, `clips.py`, selector (skip stock
  for document scenes), `pyproject.toml` (Pillow)

## Implementation Steps (TDD)
1. Tests: card size/wrap, highlight bbox inside card, filter string, scene selection caps.
2. Implement card + animation + wiring.
3. Live: one statistic-heavy topic; frame check.

## Success Criteria
- [ ] Document scenes render with legible Vietnamese text, highlight sweeps exactly the phrase.

## Risk Assessment
- Looks templated if overused → cap 2/short.
- Text overflow → auto font sizing with min size, else fall back to stock.
