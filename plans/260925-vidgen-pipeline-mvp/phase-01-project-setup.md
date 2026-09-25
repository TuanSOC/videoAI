---
phase: 1
title: Project Setup
status: completed
priority: P1
effort: 0.5d
dependencies: []
---

# Phase 1: Project Setup

## Overview
Scaffold Python package, config, shared data models, and verify external tools (FFmpeg, GPU, ComfyUI, Ollama).

## Requirements
- Functional: installable package `vidgen`, `python -m vidgen doctor` checks environment.
- Non-functional: Python 3.11+, `uv` for deps, secrets in `.env` (never committed).

## Architecture
```
video trend/
  pyproject.toml
  .env.example          # GEMINI_API_KEY, PEXELS_API_KEY, PIXABAY_API_KEY, COMFYUI_URL, OLLAMA_URL
  .gitignore            # .env, output/, cache/
  config.yaml           # voices per lang, format presets, AI caps, fallbacks
  src/vidgen/
    __main__.py  cli.py  config.py  models.py  pipeline.py
    script/  voice/  visuals/  assemble/  metadata.py
  assets/music/  assets/fonts/
  comfy_workflows/      # exported ComfyUI API-format JSON
  output/  cache/
  tests/
```
`models.py` (Pydantic): `Scene{id, narration, visual_query, visual_type: stock|ai_image|ai_video, ai_prompt}`, `Script{title, hook, lang, format, scenes, chapters?}`, `WordTiming{word, start, end}`, `Asset{scene_id, path, kind, source, license}`.

`config.yaml` format presets:
```yaml
formats:
  short: {w: 1080, h: 1920, fps: 30, target_sec: [45, 75], max_ai_video: 3}
  long:  {w: 1920, h: 1080, fps: 30, target_sec: [480, 900], max_ai_video: 6}
voices:
  vi: vi-VN-HoaiMyNeural
  en: en-US-AndrewMultilingualNeural
```

## Related Code Files
- Create: `pyproject.toml`, `.env.example`, `.gitignore`, `config.yaml`, `src/vidgen/{__init__,__main__,cli,config,models}.py`

## Implementation Steps
1. `git init`, `uv init --package`, add deps: typer, pydantic, pydantic-settings, pyyaml, httpx, edge-tts, faster-whisper, google-genai, python-slugify, rich; dev: pytest.
2. `config.py`: load `.env` + `config.yaml` into typed Settings.
3. `models.py` with schemas above.
4. `doctor` command: check `ffmpeg -version` + `h264_nvenc` encoder, `nvidia-smi`, ComfyUI `/system_stats`, Ollama `/api/tags`, API keys present. Print table; missing optional services = warning, not error.
5. Download a free font supporting Vietnamese (Be Vietnam Pro / Montserrat) into `assets/fonts/`.

## Success Criteria
- [ ] `uv run python -m vidgen doctor` runs and reports status
- [ ] `pytest` runs (empty suite OK)
- [ ] `.env` gitignored

## Risk Assessment
- FFmpeg on Windows missing NVENC build → doctor warns, fallback libx264.
