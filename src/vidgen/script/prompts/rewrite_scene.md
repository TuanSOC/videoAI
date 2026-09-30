You are polishing ONE scene of a narrated video.

Video title: $title
Language: $lang_name
$angle

Reference material:
$facts

Context only — do NOT copy or merge their content, they stay as they are:
- Previous scene: $prev
- Next scene: $next

Scene to rewrite: $current
Editor's instruction (follow it first): $instruction

Rules:
- Rewrite ONLY this scene's own idea. Never absorb what the previous or next scene says.
- 1-2 spoken sentences, at most $max_words words. If the instruction asks for shorter, use fewer words
  than the current version ($current_words).
- Keep it factual: only facts from the reference material; no invented numbers or names.
- `visual_query`: ALWAYS in English, ONE short phrase of 2-4 concrete filmable words matching the new text. When the idea itself can't be filmed, film a visual metaphor for it (write the image itself, never the word "metaphor"): loss → "hourglass sand running out", cybercrime → "eye reflecting green code", inflation → "burning banknotes", collapse → "ship sinking in storm".
- `alt_queries`: 2 MORE English stock queries for the same scene, filmed differently: one close-up of a concrete object or action (e.g. "finger tapping phone screen"), one visual metaphor or wider setting (e.g. "cracked padlock macro", "person at laptop in dark room").
- `ai_prompt`: for EVERY scene (stock too: it is drawn when no stock clip fits) — ONE English shot note like a director of photography's: camera (extreme macro / low angle / POV / aerial drone) + the subject in action (literal or the metaphor) + lighting (moody chiaroscuro / dramatic rim light / neon glow / golden hour) + setting; vertical 9:16 composition, photorealistic. No text, logos or real people's faces, and never a screen, document, sign, letter or app interface as the subject (image models turn text into gibberish) — show a physical scene or metaphor instead (a fishing hook through an envelope, a hooded figure behind frosted glass). Same rules as `visual_query`; show what is happening, not abstract ideas ("hacker" → "hooded person typing on laptop").
- `visual_type`: "stock".
