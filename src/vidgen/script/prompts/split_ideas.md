A creator pasted rough notes with one or more video ideas. Extract each distinct video topic.

Notes:
"""
$text
"""

Rules:
- One entry per distinct subject a separate video should be made about (at most $max_topics).
- Rewrite each as a short, clear topic in $lang_name (e.g. "Vì sao nước biển mặn"), keeping the creator's intent.
- Drop category headers, production notes and comments (e.g. "Thiên nhiên / địa lý:", "có cảnh sóng, rất hợp short").
- Do not invent topics that are not in the notes. If the notes describe a single idea, return exactly one topic.
