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

## Results (2026-09-30) — code done, waiting on credentials / install
- User chose both: `ai.image_sources: [comfyui, cloudflare]` → `ImageSources` tries ComfyUI (when it answers) then
  Cloudflare; AI video stays ComfyUI-only; asset source = the generator that drew ("flux" / "cloudflare").
- `visuals/cloudflare.py`: flux-2-klein-4b by default (multipart, width/height 256-1920 → true 768×1344),
  flux-1-schnell supported (JSON, square). Docs (2026-09): free plan 10,000 neurons/day; schnell ≈ 58 neurons per
  1024² image (~170/day); klein price not published → measure on first use.
- Secrets CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN (.env, studio settings, doctor row). Metadata AI
  disclosure now keyed on license/source (Cloudflare stills were missed by the old flux/wan check).
- Not yet live: no token here, ComfyUI not installed. 415 tests green.

## Live results (2026-09-30, ComfyUI installed at D:\ComfyUI)
- torch 2.14 cu126 (driver 560.92); Flux schnell Q4 GGUF; VAE from the Comfy-Org mirror (BFL repo gated, 401).
- 768×1344 still: 40 s first (model load), 26 s after; peak VRAM 6.3 GB.
- Found live and fixed: Flux resident in VRAM pushed the vision model to CPU (sourcing 524 s) → ComfyUI freed
  before the vision session, drawing deferred until after it (143 s); shot notes about screens/notices/UIs
  gave gibberish text ("FARE EANK", "WARNIN WARNING") → such notes are never drawn (stock kept).
- Phishing short (16 scenes): 1 scene drawn, rest stock ≥ 5 with judge; 59 s, 0 flags, -14.0 LUFS.
- Cloudflare still needs the user's account/token.
