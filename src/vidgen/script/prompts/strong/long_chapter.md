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

Storytelling craft (you are a strong model: write like a top documentary narrator, not a list of facts):
- Anchor the video in ONE real case from the reference material: who (a person, organisation or place),
  when, and what was at stake. Tension first, explanation after.
- Every scene carries one concrete, vivid detail (an object, a number, a place, a consequence) — no filler
  sentences like "this is very dangerous" or "let's find out".
- Vary the rhythm: a short punchy sentence, then a longer one that explains. Speak to the viewer ("bạn",
  "you") when it raises the stakes.
- Build cause → effect: each scene should make the viewer need the next one.

Facts (strict):
- You may use general knowledge to explain context and mechanisms in plain words.
- Every figure, date, name, place, organisation and quote MUST appear in the reference material.
  Never tie the topic to a country, company or person the material doesn't mention (e.g. no "in Vietnam"
  unless the material says so). If the material has no number for something, describe it without one.
- This is spoken aloud: never write URLs, domain names, file hashes, code or long IDs — describe them
  instead ("một tên miền dài vô nghĩa", "a long nonsense web address").
- Speak the viewer's language: translate technical terms ("chân trời sự kiện", not "event horizon");
  keep English only for proper names (EHT, WannaCry, Microsoft).

Rules:
- Narration language: $lang_name. Natural spoken documentary style, varied sentence length, no emojis, no stage directions, no "in this chapter".
- About $target_words words total for this chapter.
- Split into scenes of 1-2 sentences (max 25 words each).
- Facts must be accurate; never invent statistics, dates or quotes.
- `visual_query`: ALWAYS in English, ONE short phrase of 2-4 concrete filmable words for stock footage search. No commas or lists, no abstract words, no names of real people.
- `alt_queries`: 2 MORE English stock queries for the same scene, filmed differently: one close-up of a concrete object or action (e.g. "finger tapping phone screen"), one wider setting (e.g. "person at laptop in dark room"). Same rules as `visual_query`; show what is happening, not abstract ideas ("hacker" → "hooded person typing on laptop").
- `visual_type`: "stock" by default. Allowed "ai_video" scenes in this chapter: $max_ai_video (only for scenes that cannot exist as real footage). "ai_image" when stock is unlikely but motion is not essential.
- `ai_prompt`: photorealistic English description for ai_image/ai_video, empty string for stock.
