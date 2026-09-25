---
phase: 6
title: CLI and Review Flow
status: completed
priority: P1
effort: 0.5d
dependencies:
  - 2
  - 3
  - 4
  - 5
---

# Phase 6: CLI and Review Flow

## Overview
Wire stages into a resumable pipeline with a human review pause after script generation.

## Requirements
- Functional:
  - `vidgen make "<topic>" --format short|long --lang vi|en [--no-review] [--force STAGE]`
  - `vidgen resume <slug>` continues from first missing artifact
  - `vidgen doctor`
- Non-functional: progress output (rich), per-stage timing log to `output/<slug>/run.log`.

## Architecture
`pipeline.py`: ordered stages `[script, voice, visuals, render, metadata]`, each `(name, artifact_path, fn)`. Run loop: skip if artifact exists (unless `--force`), else run. Review defaults ON: after `script`, print path of `script.json` + summary table and exit; user edits narration / visual_query then `resume`.
Edit detection: store hash of `script.json` in `state.json`; if changed on resume → invalidate downstream artifacts (voice/visuals/render) automatically.

## Related Code Files
- Create: `src/vidgen/pipeline.py`
- Modify: `src/vidgen/cli.py`

## Implementation Steps
1. Stage registry + runner with skip/force/invalidate logic.
2. `make`, `resume`, `doctor` commands in typer.
3. Slug = slugify(topic)[:50] + short date; collision → suffix.
4. Final summary: paths, duration, AI assets used, elapsed per stage.

## Success Criteria
- [ ] `make` stops after script by default; `resume` completes to MP4
- [ ] Editing a scene in `script.json` then `resume` regenerates voice/visuals
- [ ] Crash mid-visuals → `resume` doesn't redo script/voice

## Risk Assessment
- Partial invalidation too granular = complexity → MVP invalidates whole downstream, not per-scene (YAGNI).
