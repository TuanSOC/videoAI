---
phase: 1
title: DP prompting engine
status: completed
priority: P1
effort: 0.5d
dependencies: []
---

# Phase 1: DP prompting engine

## Overview
Scripts ask for visuals like a director of photography: metaphors where the literal idea can't be filmed,
and a full cinematic `ai_prompt` for every scene (so an AI fallback draws exactly that scene).

## Requirements
- `visual_query`: still 2-4 concrete filmable English words (stock search), but a visual metaphor/symbol when
  the literal idea is abstract ("loss" → "hourglass sand running out"; "cybercrime" → "eye reflecting green
  code"; "inflation" → "burning banknotes"). `alt_queries`: one literal close-up + one metaphor or wide shot.
- `ai_prompt` for EVERY scene (stock too): [camera] + [subject/metaphor in action] + [lighting] + [setting],
  vertical composition, photorealistic, no text/logos/real people's faces.
- Base prompts (8B) get the same rules briefly; strong prompts the metaphor map and camera/lighting vocabulary.
- Code: split scenes keep the scene's ai_prompt (not only the first part).

## Tests
- Every prompt that asks for scenes asks for ai_prompt on every scene and mentions metaphors.
- postprocess keeps ai_prompt on stock scenes and on every split part.

## Success Criteria
- [ ] Live Groq short: every scene has an ai_prompt with camera + lighting words; ≥2 metaphor queries.
- [ ] Suite green.
