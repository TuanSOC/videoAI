You are writing one chapter of a documentary-style YouTube video.

Video title: $title
Full outline:
$outline

Write chapter $chapter_index of $chapter_count: "$chapter_title"
Chapter summary: $chapter_summary
$position_note

Reference material:
$facts

Rules:
- Narration language: $lang_name. Natural spoken documentary style, varied sentence length, no emojis, no stage directions, no "in this chapter".
- About $target_words words total for this chapter.
- Split into scenes of 1-2 sentences (max 25 words each).
- Facts must be accurate; never invent statistics, dates or quotes.
- `visual_query`: ALWAYS in English, ONE short phrase of 2-4 concrete filmable words for stock footage search. No commas or lists, no abstract words, no names of real people.
- `visual_type`: "stock" by default. Allowed "ai_video" scenes in this chapter: $max_ai_video (only for scenes that cannot exist as real footage). "ai_image" when stock is unlikely but motion is not essential.
- `ai_prompt`: photorealistic English description for ai_image/ai_video, empty string for stock.
