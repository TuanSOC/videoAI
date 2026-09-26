---
phase: 6
title: Mood Music
status: in-progress
priority: P2
effort: 2h
dependencies: []
---

# Phase 6: Mood Music

## Overview
Music chosen by mood from `assets/music/<mood>/`; the script LLM tags the video's mood. Random start
offset, fade in/out, existing sidechain ducking.

## Requirements
- Functional:
  - MOODS = tense, calm, upbeat, mystery, inspiring. `Script.mood: str = ""` (prompt asks for one of MOODS;
    invalid → "").
  - `pick_music(music_dir, seed, mood)`: tracks in `<mood>/` first, else any track anywhere, else None.
    Deterministic per seed (re-render keeps track).
  - Start offset: seeded random in [0, max(0, track_len - video_len - 1)]; `afade=in:d=1.5`,
    `afade=out:st=video_len-2:d=2` on music branch only.
  - Create empty mood folders with a README.txt listing sources (YouTube Audio Library, Pixabay Music).
- Non-functional: no music → unchanged render path.

## Related Code Files
- Modify: `src/vidgen/assemble/music.py`, `src/vidgen/assemble/render.py`, `src/vidgen/models.py`,
  `src/vidgen/script/writer.py` + prompts (short.md, long_outline.md), `assets/music/*/README.txt`
- Tests: `tests/test_assemble.py`

## Implementation Steps (TDD)
1. Tests first:
   - pick_music prefers mood folder; falls back to any; None when empty; deterministic.
   - final_args with music: `-ss <offset>` on music input, afade in/out in audio graph, still two
     filter graphs (NaN fix pinned).
   - writer: invalid mood → "".
2. Implement.
3. Full suite.

## Success Criteria
- [ ] Tests pass; with one track in `tense/`, phishing render has music ducked under voice.

## Risk Assessment
- Track shorter than video: `-stream_loop -1` already present; offset 0 then.
- Needs user to supply tracks; without them feature is inert (documented).
