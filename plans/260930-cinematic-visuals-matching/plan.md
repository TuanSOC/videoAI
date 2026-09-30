---
title: Cinematic visuals & content matching
description: >-
  DP-style visual prompts and metaphors, a vision quality gate with AI fallback,
  a $0 AI image source, unified grading looks
status: pending
priority: P1
branch: main
tags:
  - visuals
  - prompts
  - selector
  - grading
blockedBy: []
blocks: []
created: '2026-09-30T08:07:49.354Z'
createdBy: 'ck:plan'
source: skill
---

# Cinematic visuals & content matching

## Overview
Context and live findings: `plans/reports/brainstorm-260930-cinematic-visuals-matching.md`.
Pollinations (the spec's $0 source) failed live (low-res Sana, watermark, then 402), so the AI source is its
own phase, chosen later; the quality gate only raises the bar when a generator is actually available.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [DP prompting engine](./phase-01-dp-prompting-engine.md) | Completed |
| 2 | [Quality-gated selection](./phase-02-quality-gated-selection.md) | Completed |
| 3 | [Zero-cost AI image source](./phase-03-zero-cost-ai-image-source.md) | Pending (source to choose) |
| 4 | [Unified aesthetic grading](./phase-04-unified-aesthetic-grading.md) | Completed |

## Constraints
$0; RTX 4060 8 GB (no VRAM fight with Ollama); short render ≤ 4 min; full suite green (379 now).
