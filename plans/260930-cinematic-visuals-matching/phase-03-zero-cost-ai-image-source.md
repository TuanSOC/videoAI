---
phase: 3
title: "Zero-cost AI image source"
status: pending
priority: P1
effort: "0.5-1d"
dependencies: [2]
---

# Phase 3: Zero-cost AI image source

## Overview
A generator for the phase 2 gate. Pollinations ruled out live (580×1015 Sana, watermark, 402 after one image).

## Candidates (user to choose)
- ComfyUI local + Flux schnell (integrated already; install ComfyUI + ~12 GB models; ~20-40 s/img on 4060 8 GB;
  unload Ollama models first — vision_session already does).
- Cloudflare Workers AI `@cf/black-forest-labs/flux-1-schnell` free daily allocation (account + token; measure
  images/day before relying on it).
- Gemini image with the existing key — only if its project is confirmed free tier (else ~$0.04/img).

## Requirements (whichever source)
- Same `image(prompt, out) -> Path` interface as `AIGenerator`; cached like other assets; 9:16 at ≥ 768×1344;
  failures fall through the chain; doctor shows the source's status.
