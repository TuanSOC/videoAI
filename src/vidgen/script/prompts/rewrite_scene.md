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
- `visual_query`: ALWAYS in English, ONE short phrase of 2-4 concrete filmable words matching the new text.
- `visual_type`: "stock".
