# Brainstorm 260930 — Cinematic Visuals & Content Matching

## Problem (user spec)
Stock clips generic (office/nature), literal queries ("person typing on laptop"), weak clips kept when the
vision judge scores low, mismatched colour across clips. Want: DP-style visuals + metaphors, quality gate ≥7
with AI fallback, a $0 cloud image provider, unified grading presets, 2.5D motion on AI stills.

## Verified facts (2026-09-30, live)
- selector chain today: stock video → stock photo → **AI image** → weak clip (vision ≥3) → placeholder.
  The AI step exists; it never runs because ComfyUI is off (`self.ai is None`; not installed here).
- Vision scores, 19 stock picks in 2 videos: 7× score 5, 7× 8, 2× 9, 3× 10 → bar 7 sends ~37 % of scenes to AI.
- **Pollinations anonymous: NOT viable.** 1st call 200 in 3.3 s but 580×1015, model "sana" (not Flux), blurry,
  pollinations.ai logo burned in despite nologo=true; every later call (also after 20 s) 402 Payment Required.
- RTX 4060 Laptop 8 GB; GEMINI_API_KEY set (image gen may bill ~$0.04/img); no ComfyUI install found.
- Pexels/Pixabay search works on 2-4 concrete words; long cinematic strings return nothing useful.

## Decisions (user)
1. Phase 1 first; the AI source decided later (ComfyUI local / Cloudflare Workers AI free tier / Gemini).
2. Stock bar 7 only when an AI generator is available; without one keep the best stock clip (≥5) as today.

## Design
- **Phase 1 DP prompting**: `visual_query` stays 2-4 concrete words but may be a visual metaphor when the
  literal idea is abstract/unfilmable (hourglass sand falling, burning banknotes, eye reflecting code — these
  exist in stock); `alt_queries` = one literal close-up + one metaphor/wide. `ai_prompt` filled for EVERY scene
  with the 4-part formula [camera: macro/low-angle/POV/drone] + [subject/metaphor in action] + [lighting:
  chiaroscuro/rim/neon] + [setting], vertical 9:16 composition, no text/logos/real people — so the AI fallback
  (when present) draws the exact scene. Strong prompts get the full DP guidance + metaphor map; base prompts
  the same rules, shorter.
- **Phase 2 quality gate**: with a generator: stock ≥7 → AI image → stock ≥5 → weak → placeholder.
  Without: unchanged (≥5). Judged clips reused between passes (no extra vision calls).
- **Phase 3 AI source** (pending choice): pluggable `image(prompt, out)`; ComfyUI existing; candidates
  Cloudflare Workers AI flux-1-schnell free tier (measure quota), Gemini (only if free tier confirmed).
- **Phase 4 grading presets**: `look` per video (dark_mystery / cyber_tech / vintage_archive) replacing the
  single GRADE; picked from script mood/topic; eq/colorbalance/curves + vignette + grain.
- Pillar 5 (2.5D parallax roll/tilt) deferred until AI stills exist in videos.

## Risks
- Metaphor queries can drift from the narration → vision judge still scores against the narration.
- AI stills of real events/people can mislead → prompt forbids real people/logos/text; metadata already
  discloses AI visuals.
- 37 % AI stills = less real motion → Ken Burns + (later) parallax; bar tunable in config.
