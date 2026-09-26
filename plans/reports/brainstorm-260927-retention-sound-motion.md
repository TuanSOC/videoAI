# Brainstorm 260927 — Studio-grade retention: SFX, open-loop scripts, document highlighter

## Goal
Less "cheap AI" feel, better retention. Three features; implement Phase 1 now, plan 2-3.

## Decisions (user)
| Topic | Choice |
|---|---|
| SFX source | synthesise with FFmpeg (whoosh/impact/pop, 3 variants each) into assets/sfx/{whooshes,impacts,pops}/; user files in those folders take precedence |
| Density | subtle with caps: whoosh only at real dissolves, ≥4 s apart; impact ≤2/video ≥8 s apart (hook first); pop ≤4 ≥3 s apart; cues <0.6 s apart → keep impact>whoosh>pop |
| Mix | music at −18 dB vs voice (volumedetect-based gain) + gentle sidechain; SFX ≈ −8 dB vs voice; alimiter before loudnorm −14 LUFS; keep separate video/audio filter graphs (NaN fix) |
| Control | config sfx.enabled (default true), sfx.density minimal/subtle/dense; cues written to sfx.json; no UI this round |

## Phase 1 design (SFX)
- `assemble/sfx.py`: `Cue`, `detect_cues(script, timeline, cut_times, density)`, `ensure_library()`, `build_sfx_track(cues, duration, out)`.
- Cut times from `clips.plan_shots` (`transition_in` shots) — whoosh 0.25 s before the cut.
- Impact lexicon vi/en (shock words), hook scene prioritised. Pop on words with digits (`_is_number`).
- `render.py`: extra input sfx.wav; music gain from measured means; sfx.wav + sfx.json in render stage extras.
- Tests `tests/test_sfx.py`: detection, caps, spacing, collisions, library synth + override, track length, final args, disabled config. No live services.

## Phase 2 (plan only) — open loop + 3 s hook
- Conflict: prompt ends with a comment question vs "answer in last 5 s" → hook (paradox) → open question (~5 s, scene 2) → … → payoff in second-to-last scene → very short CTA last.
- LLM fields `open_loop`, `payoff_scene`; banned generic openers list; one hook rewrite if violated. Brief angles follow same hook rules.
- Honest: no prompt guarantees >60 % retention; only structure is verifiable.

## Phase 3 (plan only) — document highlighter
- Pillow (+ Be Vietnam Pro; OpenCV can't draw Vietnamese diacritics). Paper card with a sourced quote, highlighter sweep via FFmpeg drawbox time expression.
- Ethics/risk: never imitate real publications (no real mastheads/logos/dates); only real sourced text with source name.

## Risks
- Synth SFX sound "clean/minimal", not cinematic; user can drop real CC0 files.
- Loudness: SFX transients vs loudnorm dynamic mode → limiter first; verify LUFS + true peak live.
- Over-triggering on shock words in every sentence → caps + spacing.

## Success criteria (Phase 1)
- Suite 100 % green; new tests cover all rules.
- Live render: cues in sfx.json respect caps; integrated −14 ±1 LUFS, true peak ≤ −1 dBTP; music ≈ −18 dB under voice (measured in a speech window); no audible clipping.
