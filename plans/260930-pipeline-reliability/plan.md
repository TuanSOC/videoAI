---
title: Pipeline reliability and review fixes
description: >-
  One owner per video folder (OS file lock), no stuck jobs, no cross-process
  races; the 10 review findings
status: pending
priority: P1
branch: main
tags:
  - reliability
  - pipeline
  - review
blockedBy: []
blocks: []
created: '2026-09-30T14:01:07.074Z'
createdBy: 'ck:plan'
source: skill
---

# Pipeline reliability and review fixes

## Overview
Context: `plans/reports/brainstorm-260930-pipeline-reliability.md`. TDD: a failing test per finding first.

## Phases

| Phase | Name | Status |
|-------|------|--------|
| 1 | [Folder lock and job ownership](./phase-01-folder-lock-and-job-ownership.md) | Completed |
| 2 | [Visual pipeline fixes](./phase-02-visual-pipeline-fixes.md) | Pending |
| 3 | [Script and series fixes](./phase-03-script-and-series-fixes.md) | Pending |
| 4 | [Live verification](./phase-04-live-verification.md) | Pending |

## Pipeline contract after this plan
1. Whoever runs stages on a folder holds `.vidgen.lock` (OS lock; freed on exit, crash, kill, Ctrl-C).
2. Busy ⇔ lock held. job.json = status for the UI only. Studio start marks a job interrupted only if unlocked.
3. Writes to state.json from the studio (look) refuse while the lock is held elsewhere.
4. Every external wait has a bound: edge-tts ~1 min, Groq 429 ≤ 65 s, ComfyUI image 180 s, video 1800 s.
