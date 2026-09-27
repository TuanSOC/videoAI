---
phase: 3
title: Document Highlighter
status: completed
priority: P3
effort: 5h
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

## Results (2026-09-27)
- Shipped as a code-chosen scene (not an LLM `visual_type`): a figure (2+ digits or %) in the narration that
  a research source sentence contains → card quoting that sentence verbatim + "Source: Wikipedia — <article>";
  hook/closing scenes excluded, 2 per short / 4 per long, spaced ≥3 scenes; no source sentence → no card.
- drawbox evaluates w once (verified), so the sweep is drawn per frame with Pillow; the card is a normal
  video clip (source "document"), one shot from 0 s, no exposure correction (grading turned the paper grey).
- 296 tests green (tests/test_document.py: 11). Live: "những con số" video → scene 3 card quoting
  Wikipedia "Cybercrime" (23 April 2013, AP Twitter hack), sweep over "2013," then held; captions clear.
- Limits: the quote is in the source's language (an English sentence on a Vietnamese video when only the en
  article has the figure); a research outage (Wikipedia TLS timeouts seen live) means no sources → no cards.
