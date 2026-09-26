---
phase: 1
title: SFX Engine
status: completed
priority: P1
effort: 4h
dependencies: []
---

# Phase 1: SFX Engine

## Overview
Automatic, restrained sound design: whoosh at real scene dissolves, impact on the hook / shock words,
pop on numbers. Sounds are synthesised with FFmpeg (no licence risk); user files override. Music sits
−18 dB under the voice; everything passes a limiter before the existing −14 LUFS loudnorm.

## Requirements
- Functional:
  - `Cue(kind, time, variant)`; kinds `whoosh | impact | pop`.
  - `detect_cues(script, timeline, cut_times, density="subtle") -> list[Cue]`:
    - whoosh: `cut - WHOOSH_LEAD (0.25 s)` for each dissolve cut; spacing ≥ 4 s.
    - impact: first word of scene 1 (hook) + first shock-lexicon word per sentence (vi/en list);
      cap 2, spacing ≥ 8 s, hook first.
    - pop: start of words with digits (`subtitles._is_number`); cap 4, spacing ≥ 3 s.
    - collision: cues < 0.6 s apart → keep priority impact > whoosh > pop.
    - density presets: minimal (impact hook only + ≤2 whoosh), subtle (above), dense (no caps, spacing 1 s).
    - deterministic variant choice (seeded by slug).
  - `ensure_library(root)`: for each kind folder, if no audio file exists, synthesise 3 variants (wav
    48 kHz stereo) with ffmpeg lavfi; any user audio file present is used instead.
  - `build_sfx_track(cues, library, duration, out) -> Path | None`: one ffmpeg call, `adelay` per cue,
    per-kind gain, `amix normalize=0`, padded/trimmed to duration; None when no cues.
  - render: optional 4th audio input `sfx.wav`; music gain = voice_mean − 18 dB − music_mean (from
    `volumedetect`), gentle sidechain (ratio 3); SFX mixed into the voice bus → `alimiter` → loudnorm.
  - render stage writes `sfx.json` (cues) and `sfx.wav`; both are render-stage extras.
  - config `sfx: {enabled: true, density: subtle}`.
- Non-functional: +≤5 s render time; integrated −14 ±1 LUFS, true peak ≤ −1 dBTP; no new network use.

## Architecture
```
render_video
  ├─ render_segments → shots (plan_shots) ──┐ cut_times = starts of shots with transition_in
  ├─ detect_cues(script, timeline, cut_times) → sfx.json
  ├─ ensure_library(assets/sfx) → build_sfx_track → sfx.wav
  └─ final_args(duration, music, start, music_gain_db, sfx=Path|None)
        graph A: [0:v] ass
        graph B: voice(+sfx) → alimiter → mix with ducked music → loudnorm
```
`render_segments` returns segment paths today → also expose the plan (return shots or a helper
`cut_times(shots, fps)`).

## Related Code Files
- Create: `src/vidgen/assemble/sfx.py`, `tests/test_sfx.py`, `assets/sfx/{whooshes,impacts,pops}/.gitkeep`
- Modify: `src/vidgen/assemble/render.py`, `src/vidgen/assemble/clips.py` (cut times),
  `src/vidgen/config.py`, `config.yaml`, `src/vidgen/pipeline.py` (render extras), `.gitignore`
  (generated sfx wavs)

## Implementation Steps (TDD)
1. Tests first (`tests/test_sfx.py`):
   - whoosh at cut−0.25 s, spacing drops a second cut 2 s later.
   - impact on hook + shock word; cap 2; spacing 8 s; vi and en lexicon.
   - pop on "1.5 triệu", "2016", "30%"; cap 4; spacing 3 s.
   - collision priority; density minimal/dense; disabled config → no cues/no sfx input.
   - ensure_library synthesises 3 per kind (ffmpeg, skip if missing); user file overrides synth.
   - build_sfx_track: output duration == requested; None for no cues.
   - final_args: sfx input present only when given; audio graph has alimiter, music gain; still two
     `-filter_complex` graphs.
   - pin: existing render/music tests.
2. Implement sfx.py → clips cut times → render wiring → config/pipeline.
3. Full suite; live render of an existing video: listen-check via level measurement (LUFS/TP, music
   level in a speech window, cue count vs sfx.json).

## Success Criteria
- [ ] Suite 100 % green; all rules covered.
- [ ] Live: caps respected, −14 ±1 LUFS, TP ≤ −1 dBTP, music ≈ −18 dB under voice, no clipping.

## Risk Assessment
- Synth SFX sound minimal, not cinematic → user override folders.
- Many adelay inputs (dense) → single ffmpeg call still fine (<20 cues).
- volumedetect on long music adds ~1 s → acceptable.

## Results (2026-09-27)
- 223 tests green (13 new in tests/test_sfx.py).
- Live, 2 videos: ransomware (en, 50 s) impact + 4 whoosh; phishing (vi, 33 s) 2 impact + 3 whoosh.
  −14.5 / −14.1 LUFS, true peak −1.4 / −1.5 dBFS, SFX peaks −13.3 dBFS (≈7 dB under voice peaks),
  music 18.5 dB under the voice mean. Render +~3 s.
- Tuned live: subtle whoosh spacing 4 → 10 s (was 9 whooshes/51 s), SFX gains +6 dB (were ~13 dB under
  voice peaks, barely audible).
