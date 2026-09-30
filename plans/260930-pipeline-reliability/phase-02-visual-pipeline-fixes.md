---
phase: 2
title: "Visual pipeline fixes"
status: pending
priority: P1
effort: "0.5d"
dependencies: []
---

# Phase 2: Visual pipeline fixes

## Requirements
- Finding 1: drawn stills saved as `visuals/scene_NNN_ai.*` (never the stock fallback's hardlinked path);
  scan cache/files for files shared with AI outputs.
- Finding 4: NO_TEXT covers "no visible/readable/any text", "text-free", "without (any) writing/letters";
  TEXT_SUBJECT drops "typing on", "books".
- Finding 8: at a scene change an impact beats a whoosh; `assign_transitions` reports the impacts it used;
  `punch_times` = impacts not used (a card's slideup does not use an impact).
- H: ComfyUI image timeout 180 s (config `ai.image_timeout_seconds`), video keeps `timeout_seconds`.

## Tests
- stock file hardlinked at scene_001.jpg + drawing → cache file bytes unchanged, asset path *_ai.*.
- phrases from the review are drawn; screens/notices still refused.
- impact 0.2 s after a cut with a whoosh at cut-0.09 → zoomin/fadeblack; impact on a card → punch.
