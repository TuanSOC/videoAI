---
phase: 2
title: Prompt tiers and strong prompts
status: completed
priority: P1
effort: 4h
dependencies:
  - 1
---

# Phase 2: Prompt tiers and strong prompts

## Overview
A provider has a `tier` ("strong" for groq/gemini, "base" for ollama). Prompts can be rendered per tier:
`prompts/strong/<name>.md` when it exists and the serving provider is strong, else `prompts/<name>.md`.

## Requirements
- `LLMChain.generate(prompt: str | Callable[[str], str], schema)`: callable is called with the provider's tier
  (cached per tier within one call). Plain `str` unchanged → all other callers untouched.
- `templates.render(name, tier="base", **values)`: strong file if present, else base (same placeholders —
  a strong prompt must not need a value the base call site lacks; test enforces placeholder parity).
- Call sites switched to `lambda tier: render(..., tier=tier, ...)`: writer short draft, long outline/chapter,
  expand, rewrite_scene, hook_fix; brief_angles.
- Strong prompts (`src/vidgen/script/prompts/strong/`):
  - `short.md`: storytelling — a person/organisation, a moment, a conflict; 4–6 concrete facts; one vivid
    detail per scene; sentence rhythm varies (short punch + longer explanation); may add general context from
    own knowledge, but every figure, date, name, quote must appear in the reference material; keep all
    structural rules (hook ≤12 words no generic opener, open_loop, payoff_scene, CTA question, digits,
    visual_query/alt_queries English rules, visual_type, mood, title).
  - `long_outline.md`, `long_chapter.md`: chapters with a turning point each; same fact rule.
  - `brief_angles.md`: angles must be specific (who/when/why-it-matters), not generic "lịch sử của X".
  - `expand.md`, `rewrite_scene.md`, `hook_fix.md`: same rules, tone upgrades only.
- Code-side guarantees stay (hooks.py generic opener cut, open-loop enforcement, word limits, fact-check).

## Related Code Files
- Modify: `src/vidgen/script/llm.py`, `src/vidgen/script/templates.py`, `src/vidgen/script/writer.py`,
  `src/vidgen/script/brief.py`
- Create: `src/vidgen/script/prompts/strong/*.md`, tests in `tests/test_llm_groq.py` (tier) and
  `tests/test_quality.py` (placeholder parity)

## Implementation Steps (TDD)
1. Tests first:
   - chain with fake strong provider failing → base provider receives the BASE prompt text; strong provider
     received the STRONG text.
   - `render("short.md", tier="strong")` returns strong file; unknown strong file → base.
   - parity: for each strong file, `$placeholders` ⊆ base file's placeholders.
   - writer short with fake strong provider: prompt contains a strong-only marker line.
2. Implement tiers; write strong prompts (English instructions, `$lang_name` output as today).
3. Offline sanity: render every strong prompt with sample values (no KeyError).

## Success Criteria
- [ ] Tests green; base prompts byte-identical (Ollama path unchanged).
- [ ] Strong short prompt keeps every structural rule of the base one (reviewed side by side).

## Risk Assessment
- Richer prose → more unsourced claims → fact-check (checker role) flags; prompt makes the figure rule explicit.
- Longer prompts eat TPM → keep strong prompts ≤ ~1.3× base length.
