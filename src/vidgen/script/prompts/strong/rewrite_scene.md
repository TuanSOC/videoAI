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

Style: concrete and vivid — one specific detail per scene, varied sentence rhythm, no filler.
Every figure, date, name, place and organisation must appear in the reference material; never add a country,
company or person it doesn't mention.
- This is spoken aloud: never write URLs, domain names, file hashes, code or long IDs — describe them
  instead ("một tên miền dài vô nghĩa", "a long nonsense web address").
- Speak the viewer's language: translate technical terms ("chân trời sự kiện", not "event horizon");
  keep English only for proper names (EHT, WannaCry, Microsoft).

Rules:
- Rewrite ONLY this scene's own idea. Never absorb what the previous or next scene says.
- 1-2 spoken sentences, at most $max_words words. If the instruction asks for shorter, use fewer words
  than the current version ($current_words).
- Keep it factual: only facts from the reference material; no invented numbers or names.
- `visual_query`: ALWAYS in English, ONE short phrase of 2-4 concrete filmable words matching the new text. When the idea itself can't be filmed, film a visual metaphor for it (write the image itself, never the word "metaphor"): loss → "hourglass sand running out", cybercrime → "eye reflecting green code", inflation → "burning banknotes", collapse → "ship sinking in storm".
- `alt_queries`: 2 MORE English stock queries for the same scene, filmed differently: one close-up of a concrete object or action (e.g. "finger tapping phone screen"), one visual metaphor or wider setting (e.g. "cracked padlock macro", "person at laptop in dark room").
- `ai_prompt`: for EVERY scene (stock too: it is drawn when no stock clip fits) — ONE English shot note like a director of photography's: camera (extreme macro / low angle / POV / aerial drone) + the subject in action (literal or the metaphor) + lighting (moody chiaroscuro / dramatic rim light / neon glow / golden hour) + setting; vertical 9:16 composition, photorealistic. No text, logos or real people's faces. Same rules as `visual_query`; show what is happening, not abstract ideas ("hacker" → "hooded person typing on laptop").
- `visual_type`: "stock".
