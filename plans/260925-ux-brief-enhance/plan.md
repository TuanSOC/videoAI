---
title: 'UX: Brief step, research accuracy, clip swap, job persistence'
description: >-
  AI brief step (3 angles + sources, multi-topic split), correct Wikipedia
  article selection, job persistence across restarts, length indicator,
  per-scene clip swap
status: pending
priority: P2
branch: main
tags:
  - ux
  - web
  - llm
  - research
blockedBy: []
blocks: []
created: '2026-09-25T08:33:14.660Z'
createdBy: 'ck:plan'
source: skill
---

# UX: Brief step, research accuracy, clip swap, job persistence

## Overview
Source: [brainstorm report](../reports/brainstorm-260925-ux-brief-enhance.md). Evidence: pasted multi-topic text produced a mixed script grounded in a Norwegian fairy tale + a crocodile article.

New user flow:
```
Ideas textarea (1..5 ideas) ─► [Phân tích] ─► split into N videos (status "brief")
  └─ brief job per video: 3 angles (explain / myth-bust / story) + Wikipedia sources per angle
Video page: pick & edit an angle, untick bad sources ─► script job (grounded on chosen angle)
Review script (+ length bar) ─► Render ─► swap bad clips per scene ─► re-render only
Server restart mid-job ─► "Bị gián đoạn — Tiếp tục"
```

Key decisions:
- Job state lives in its own `job.json` per video (not state.json) — pipeline rewrites state.json from an in-memory dict, a shared file would clobber.
- Research candidate selection = top-5 search hits judged by LLM (can answer "none").
- Brief/idea split runs synchronously only for the cheap split call; angle+research generation is a background job.
- Clip swap never re-runs the LLM: metadata keeps the LLM summary separately so credits can be rebuilt deterministically.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Job Persistence](./phase-01-job-persistence.md) | Completed |
| 2 | [Research Article Selection](./phase-02-research-article-selection.md) | Completed |
| 3 | [Brief Step and Multi-Topic](./phase-03-brief-step-and-multi-topic.md) | Pending |
| 4 | [Length Indicator](./phase-04-length-indicator.md) | Pending |
| 5 | [Per-Scene Clip Swap](./phase-05-per-scene-clip-swap.md) | Pending |

Order: 1 → 2 → 3 (needs 1 for brief jobs, 2 for per-angle research) → 4 → 5. Total ≈ 2.5-3 days.

## Acceptance
- Paste the 2-topic text again → 2 videos; sources Seawater/Nước biển + Aurora; no fairy tale/crocodile
- "bạch tuộc" → 3 clearly different angles
- Kill server mid-render → "Bị gián đoạn – Tiếp tục" → resume skips finished stages
- Swap clip of scene 3 (short) → new, non-duplicate clip; re-render <30s; other scenes unchanged
- Length bar amber when <45s or >75s (short)
- All existing tests still pass; new tests per phase

## Out of scope
Visual preview before render, parallel render queue, ComfyUI.

## Dependencies
Related (not blocking): [260925-vidgen-pipeline-mvp](../260925-vidgen-pipeline-mvp/plan.md) — its phase 7 (E2E) should run after this plan since the create flow changes.
