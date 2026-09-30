---
phase: 1
title: Folder lock and job ownership
status: completed
priority: P1
effort: 0.5d
dependencies: []
---

# Phase 1: Folder lock and job ownership

## Overview
Review findings 2, 3, 9: stale "running", studio blind to series, set_look racing state.json.

## Requirements
- `vidgen/folderlock.py`: `FolderLock(out_dir)` context manager; non-blocking acquire of `.vidgen.lock`
  (msvcrt.locking / fcntl.flock) → `FolderBusyError` when another process (or this one) holds it; `is_locked(d)`.
  Released by the OS if the holder dies. Writes pid/kind/time into the file for humans.
- Holders: studio JobQueue worker around `work(job)`; CLI `resume`/`make` stage runs; `series._finish`.
- `ensure_not_busy` = lock check (keeps its message). Studio submit: 409 if locked elsewhere; startup
  `mark_interrupted` only when unlocked; `job_of`/busy views treat a locked folder as running.
- series `_finish`: finally also covers BaseException (Ctrl-C → job "interrupted").
- set_look: refuse (409) while locked.

## Tests
- two locks on one folder: second raises; release → acquirable; lock freed when the holder process exits
  (subprocess test); a stale job.json "running" without lock is not busy.
- studio: submit refused when a foreign process holds the lock; startup leaves a locked folder's job alone.
- series: KeyboardInterrupt inside _finish → job "interrupted", lock released.
