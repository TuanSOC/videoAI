You are extending a video script that is too short.

Language: $lang_name
Every new narration MUST be written in $lang_name, like the current scenes — never in English
unless the language is English.
$angle

Reference material:
$facts

Current scenes (numbered, in order):
$scenes

The script has $current_words words; it needs about $target_words.
Style: concrete and vivid — one specific detail per scene, varied sentence rhythm, no filler.
Every figure, date, name, place and organisation must appear in the reference material; never add a country,
company or person it doesn't mention.
- This is spoken aloud: never write URLs, domain names, file hashes, code or long IDs — describe them
  instead ("một tên miền dài vô nghĩa", "a long nonsense web address").
- Speak the viewer's language: translate technical terms ("chân trời sự kiện", not "event horizon");
  keep English only for proper names (EHT, WannaCry, Microsoft).

Rules:
- Keep the story order: a new scene must fit logically between its neighbours (cause before effect,
  earlier dates before later ones); never a second question next to an existing one.
- Write ONLY about $new_scenes NEW scenes; do not repeat or rewrite the current ones.
- For each new scene set `after` to the number of the current scene it should follow. Spread them
  where they fit the flow best (not all at the end). $placement
- Each new scene: 1-2 sentences (max 25 words) with a concrete fact from the reference material that
  the script does not state yet. No filler, no repetition, no invented numbers.
- `visual_query`: ALWAYS in English, ONE short phrase of 2-4 concrete filmable words. `visual_type`: "stock". When the idea itself can't be filmed, film a visual metaphor for it (write the image itself, never the word "metaphor"): loss → "hourglass sand running out", cybercrime → "eye reflecting green code", inflation → "burning banknotes", collapse → "ship sinking in storm".
- `alt_queries`: 2 MORE English stock queries for the same scene, filmed differently: one close-up of a concrete object or action (e.g. "finger tapping phone screen"), one visual metaphor or wider setting (e.g. "cracked padlock macro", "person at laptop in dark room").
- `ai_prompt`: for EVERY scene (stock too: it is drawn when no stock clip fits) — ONE English shot note like a director of photography's: camera (extreme macro / low angle / POV / aerial drone) + the subject in action (literal or the metaphor) + lighting (moody chiaroscuro / dramatic rim light / neon glow / golden hour) + setting; vertical 9:16 composition, photorealistic. No text, logos or real people's faces. Same rules as `visual_query`; show what is happening, not abstract ideas ("hacker" → "hooded person typing on laptop").
