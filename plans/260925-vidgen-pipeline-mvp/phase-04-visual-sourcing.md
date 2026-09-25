---
phase: 4
title: Visual Sourcing
status: in-progress
priority: P1
effort: 2d
dependencies:
  - 2
---

# Phase 4: Visual Sourcing

## Overview
For each scene, obtain one visual asset via fallback chain: stock video → stock photo → AI image (Flux) → AI video (Wan 2.2), honoring `visual_type` and caps.

## Requirements
- Functional: `visuals/scene_XXX.(mp4|jpg)` + `assets.json` (source, license, url, author) for every scene.
- Non-functional: orientation match (portrait for short, landscape for long); clip duration ≥ scene duration when possible; no duplicate clip reused within a video; on-disk cache keyed by query+orientation.

## Architecture
```
visuals/
  stock.py     # PexelsClient (videos+photos), PixabayClient (videos+photos); normalize → Candidate{url, w, h, duration, kind, author, license}
  comfy.py     # ComfyUIClient: POST /prompt (workflow JSON w/ injected prompt/seed/size), poll /history, GET /view
  ai_image.py  # Flux-schnell workflow → PNG
  ai_video.py  # Wan 2.2 TI2V-5B workflow (720p, ~5s) → MP4; image-to-video from Flux still for better control
  selector.py  # per-scene chain + scoring + dedupe + cache
```
Selector logic:
```
if visual_type == ai_video and ai_video_budget > 0 → ai_video (fallback ai_image on failure)
else:
  candidates = pexels_video + pixabay_video (orientation filter)
  score = orientation_match*3 + resolution_ok*2 + duration>=scene*2 - reused*10
  best ≥ threshold → download
  else stock photo → else ai_image
```
Wan 2.2 cost: ~3-8 min/clip on 12GB → hard cap per format (config `max_ai_video`). ComfyUI run separately by user (`COMFYUI_URL`); if unreachable → skip AI video, use ai_image or stock photo.

## Related Code Files
- Create: `src/vidgen/visuals/{__init__,stock,comfy,ai_image,ai_video,selector}.py`, `comfy_workflows/{flux_schnell,wan22_ti2v}.json`, `tests/test_selector.py`

## Implementation Steps
1. Pexels + Pixabay clients (httpx, API keys, respect rate limits: Pexels 200 req/h).
2. Download with cache (`cache/stock/<hash>.mp4`), pick file variant closest to target resolution (≤1080p to save disk).
3. Install ComfyUI locally + Flux-schnell (fp8) + Wan 2.2 5B models; export API-format workflows to `comfy_workflows/`.
4. ComfyUI client with node-id placeholders for prompt/seed/width/height.
5. Selector with scoring, dedupe, budget, cache; write `assets.json`.
6. Tests: selector scoring & fallback with mocked clients.

## Success Criteria
- [ ] Short (≈10 scenes) sourced in <3 min without AI video
- [ ] AI video path produces a clip when ComfyUI up; degrades gracefully when down
- [ ] `assets.json` includes license/author for every stock asset
- [ ] No watermarked assets (Pexels/Pixabay are watermark-free)

## Implementation Notes (code done, live test pending keys + ComfyUI)
- Unit-tested with mocked HTTP: Pexels/Pixabay parsing, variant pick (≤1920px), search cache, scoring, dedupe, query shortening, AI budget + fallbacks, ComfyUI queue/poll/download/error.
- Live-verified: no keys + ComfyUI offline → clean placeholder degradation.
- Downloads cached in `cache/files/`, hard-linked into `output/<slug>/visuals/`.
- ComfyUI workflow JSONs follow official Flux-schnell / Wan 2.2 5B templates with GGUF loaders (needs ComfyUI-GGUF custom node). UNVERIFIED until ComfyUI installed; model filenames in JSON must match user's files.
- AI sizes in config `ai:` tuned for 8GB: Flux 768x1344, Wan 544x960 x 81 frames.

## Risk Assessment
- Poor stock relevance for abstract topics → LLM gives concrete queries; allow user edit of `visual_query` in review step.
- VRAM contention (whisper + Flux + Wan) → run stages sequentially, free models between stages (ComfyUI `/free`).
