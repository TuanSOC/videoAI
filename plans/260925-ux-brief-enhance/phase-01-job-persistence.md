---
phase: 1
title: Job Persistence
status: completed
priority: P1
effort: 0.5d
dependencies: []
---

# Phase 1: Job Persistence

## Overview
Jobs survive server restarts: status written to `output/<slug>/job.json`; on startup, unfinished jobs become `interrupted` and the UI offers "Tiếp tục".

## Requirements
- Functional: every job status/stage change persisted; startup marks `queued|running` → `interrupted`; `POST /api/videos/{slug}/resume` re-submits the same job kind; UI pill "Bị gián đoạn" + banner button.
- Non-functional: atomic writes (`fsutil.write_atomic`); no change to pipeline's `state.json` ownership.

## Architecture
- `JobStatus` gains `out_dir` (not serialized) and a `save()` → `write_atomic(out_dir/"job.json", ...)`.
- `JobQueue` calls `job.save()` on submit, running, done/error; app's `on_stage` callback calls `job.save()` after updating stages.
- App `job_of(d)` = in-memory job if present else `load_job(d)` from job.json.
- Startup (`create_app`): scan video dirs; job.json with status in (queued, running) → rewrite `status="interrupted"`.
- `video_status`: `interrupted` precedes file-based rules.
- Resume endpoint: kind `render` → render job (no force); `script`/`brief` → same enqueue helpers as their normal endpoints.

## Related Code Files
- Modify: `src/vidgen/web/jobs.py`, `src/vidgen/web/app.py`, `src/vidgen/web/static/app.js`, `src/vidgen/web/static/app.css`
- Modify: `tests/test_web.py`

## Implementation Steps
1. Add `out_dir`, `save()`, `load_job()` to jobs.py; persist at each transition.
2. App: `job_of`, startup sweep, `interrupted` status, `/resume` endpoint.
3. UI: STATUS label, banner "Bị gián đoạn khi đang {stage}. [Tiếp tục]", resume call.
4. Tests: job.json written on done/error; new app instance over same dir reports `interrupted`; resume re-runs only missing stages.

## Success Criteria
- [ ] Kill server mid-render → reopen → "Bị gián đoạn" → Tiếp tục → finishes, earlier stages skipped
- [ ] Error jobs still show error after restart
- [ ] Tests pass

## Risk Assessment
- Stale job.json from old runs → only `queued|running` are rewritten; done/error stay informational.
