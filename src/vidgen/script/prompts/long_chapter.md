You are writing one chapter of a documentary-style YouTube video.

Video title: $title
Full outline:
$outline

Write chapter $chapter_index of $chapter_count: "$chapter_title"
Chapter summary: $chapter_summary
$position_note

$angle

Reference material:
$facts

Rules:
- Narration language: $lang_name. Natural spoken documentary style, varied sentence length, no emojis, no stage directions, no "in this chapter".
- About $target_words words total for this chapter.
- Split into scenes of 1-2 sentences (max 25 words each).
- Facts must be accurate; never invent statistics, dates or quotes.
- `visual_query`: ALWAYS in English, ONE short phrase of 2-4 concrete filmable words for stock footage search. No commas or lists, no abstract words, no names of real people. When the idea itself can't be filmed, film a visual metaphor for it (write the image itself, never the word "metaphor"): loss → "hourglass sand running out", cybercrime → "eye reflecting green code", inflation → "burning banknotes", collapse → "ship sinking in storm".
- `alt_queries`: 2 MORE English stock queries for the same scene, filmed differently: one close-up of a concrete object or action (e.g. "finger tapping phone screen"), one visual metaphor or wider setting (e.g. "cracked padlock macro", "person at laptop in dark room"). Same rules as `visual_query`; show what is happening, not abstract ideas ("hacker" → "hooded person typing on laptop").
- `visual_type`: "stock" by default. Allowed "ai_video" scenes in this chapter: $max_ai_video (only for scenes that cannot exist as real footage). "ai_image" when stock is unlikely but motion is not essential.
- `ai_prompt`: for EVERY scene (stock too: it is drawn when no stock clip fits) — ONE English shot note like a director of photography's: camera (extreme macro / low angle / POV / aerial drone) + the subject in action (literal or the metaphor) + lighting (moody chiaroscuro / dramatic rim light / neon glow / golden hour) + setting; vertical 9:16 composition, photorealistic. No text, logos or real people's faces, and never a screen, document, sign, letter or app interface as the subject (image models turn text into gibberish) — show a physical scene or metaphor instead (a fishing hook through an envelope, a hooded figure behind frosted glass).
