You are extending a video script that is too short.

Language: $lang_name
$angle

Reference material:
$facts

Current scenes (numbered, in order):
$scenes

The script has $current_words words; it needs about $target_words.
Rules:
- Write ONLY about $new_scenes NEW scenes; do not repeat or rewrite the current ones.
- For each new scene set `after` to the number of the current scene it should follow (1 to $count).
  Spread them where they fit the flow best (not all at the end). Scene 1 is the opening hook and the
  last scene is the closing question: never put a new scene before 1 or after the last one.
- Each new scene: 1-2 sentences (max 25 words) with a concrete fact from the reference material that
  the script does not state yet. No filler, no repetition, no invented numbers.
- `visual_query`: ALWAYS in English, ONE short phrase of 2-4 concrete filmable words. `visual_type`: "stock".
- `alt_queries`: 2 MORE English stock queries for the same scene, filmed differently: one close-up of a concrete object or action (e.g. "finger tapping phone screen"), one wider setting (e.g. "person at laptop in dark room"). Same rules as `visual_query`; show what is happening, not abstract ideas ("hacker" → "hooded person typing on laptop").
