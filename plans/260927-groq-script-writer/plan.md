---
title: "Groq script writer + strong prompts + topic enhance"
description: "Creative LLM tasks on Groq (gpt-oss-120b → qwen3.8-27b → Ollama), richer prompts for strong models, ✨ topic enhance"
status: pending
priority: P2
branch: "main"
tags: [llm, groq, prompts, ui]
blockedBy: []
blocks: []
created: "2026-09-27T09:27:37.022Z"
createdBy: "ck:plan"
source: skill
---

# Groq script writer + strong prompts + topic enhance

## Overview
Scripts from qwen3:8b are thin. Route creative text tasks to Groq (free tier, OpenAI-compatible, verified live:
`openai/gpt-oss-120b`, `qwen/qwen3.8-27b`, json_schema strict OK, limits 1000 req/day + 8000 tokens/min),
keep checker tasks (fact-check, clip judge) on local Ollama, give strong models their own prompt set, and add
a ✨ topic enhance button. Context: `plans/reports/brainstorm-260927-groq-script.md`.

Mode: `--tdd` — each phase writes failing tests first, then code; full suite (300) stays green.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Groq provider and role routing](./phase-01-groq-provider-and-role-routing.md) | Pending |
| 2 | [Prompt tiers and strong prompts](./phase-02-prompt-tiers-and-strong-prompts.md) | Pending |
| 3 | [Topic enhance and model badge](./phase-03-topic-enhance-and-model-badge.md) | Pending |
| 4 | [Live verification](./phase-04-live-verification.md) | Pending |

## Key decisions
- Roles: `creative` (brief angles, research plan/pick, script, expand, rewrite, hook fix, metadata, split ideas,
  topic enhance) and `checker` (fact-check, selector text judge).
- No Groq key → Groq entries skipped → exactly today's $0 Ollama behaviour.
- Fallback to Ollama re-renders the base prompt (8B never sees the strong prompt).
- Facts: model may add general context; figures/dates/names only from sources; fact-check flags the rest.
- Secret: `GROQ_API_KEY` in `.env` only (gitignored). Suggest user rotates the key (pasted in chat).

## Dependencies
None (retention plan 260927-retention-sound-motion is completed).
