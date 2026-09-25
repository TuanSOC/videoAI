You are extending a video script that is too short.

Language: $lang_name
$angle

Reference material:
$facts

Current scenes (JSON, in order):
$scenes

The script has $current_words words; it needs about $target_words.
Rules:
- Return the FULL scene list: every current scene unchanged and in the same order, plus about
  $new_scenes new scenes inserted where they fit the flow best (not all at the end; never before the
  opening hook scene, never after the closing question).
- Each new scene: 1-2 sentences (max 25 words) with a concrete fact from the reference material that
  the script does not state yet. No filler, no repetition, no invented numbers.
- `visual_query`: ALWAYS in English, ONE short phrase of 2-4 concrete filmable words. `visual_type`: "stock".
