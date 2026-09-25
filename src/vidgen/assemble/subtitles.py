"""Word timings → ASS subtitles.

Short: 1-3 big words per chunk, karaoke-filled as they are spoken (\\kf), popping in, key words
(numbers, names, the video's recurring topic words) larger and coloured. Long: 2-line chunks
balanced by characters. Chunks follow meaning: never end on a function word, keep a number with its
unit, don't strand one word before a full stop. Positions keep clear of TikTok/Shorts/Reels UI.
"""

import re
from collections import Counter

from vidgen.config import FormatPreset
from vidgen.models import Script, Timeline, WordTiming

FONT = "Be Vietnam Pro"
# ASS colours are &HAABBGGRR
WHITE, YELLOW, CYAN, BLACK = "&H00FFFFFF", "&H0000E5FF", "&H00F5D65C", "&H00000000"
HIGHLIGHT = r"{\c&H0000E5FF&}"  # long-format emphasis colour (yellow)
SENTENCE_PUNCT = (".", "!", "?", "…", ",", ";", ":")
SHORT_MAX_WORDS = 3
LONG_MAX_WORDS = 12
LONG_LINE_CHARS = 42
PAUSE_BREAK = 0.3   # a gap this long between words starts a new chunk
HOLD_AFTER = 0.25   # keep the last chunk of a pause on screen a little longer
MAX_TOPIC_WORDS = 4
POP = r"{\fscx86\fscy86\t(0,110,\fscx100\fscy100)}"  # chunk pops in over 110 ms
EMPHASIS_ON = r"{\2c" + CYAN + r"&\fscx114\fscy114}"
EMPHASIS_OFF = r"{\2c" + WHITE + r"&\fscx100\fscy100}"

# words a chunk must not end on (the listener expects what follows)
FUNCTION_WORDS = set("""
của và là những các một với cho trong để khi thì mà nhưng hay hoặc đã sẽ đang được bị có không
rất cũng này đó nào như từ vào ra lên xuống tại về theo bởi vì nên nếu còn chỉ mỗi mọi
lại rồi nữa luôn thêm vẫn đều hơn nhất quá đi
the a an of to in on at by for with from and or but is are was were be been that this these those
its their his her our your my as into than then so if when while which who whose
""".split())

STYLES = {
    # fontsize, outline, shadow, margin_l, margin_r, margin_v — tuned for 1080x1920 / 1920x1080.
    # short: right margin wider than left to clear the like/comment/share column; margin_v keeps the
    # text above the caption/music strip at the bottom of TikTok/Shorts/Reels.
    "short": (90, 7, 3, 110, 210, 620),
    "long": (58, 3, 1, 200, 200, 80),
}


def ass_time(t: float) -> str:
    cs = max(0, round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")")


def _bare(token: str) -> str:
    return "".join(ch for ch in token.casefold() if ch.isalnum())


def _is_number(token: str) -> bool:
    return any(ch.isdigit() for ch in token)


def display_words(script: Script, timeline: Timeline) -> list[WordTiming]:
    """TTS word events drop punctuation; restore it from the narration when tokens line up 1:1."""
    narration = {s.id: s.narration.split() for s in script.scenes}
    out: list[WordTiming] = []
    for sa in timeline.scenes:
        tokens = narration.get(sa.scene_id, [])
        # equal counts alone can be a coincidence ("—" token vs merged number) → compare text too
        if len(tokens) == len(sa.words) and all(_bare(t) == _bare(w.word) for t, w in zip(tokens, sa.words)):
            out += [w.model_copy(update={"word": t}) for w, t in zip(sa.words, tokens)]
        else:
            out += sa.words
    return out


def emphasis_words(script: Script) -> set[str]:
    """Bare words to emphasise: numbers, capitalised names mid-sentence, and the few content words
    the narration keeps coming back to (the topic: "bạch", "tuộc")."""
    marked: set[str] = set()
    counts: Counter[str] = Counter()
    for scene in script.scenes:
        tokens = scene.narration.split()
        for i, tok in enumerate(tokens):
            b = _bare(tok)
            if not b:
                continue
            if _is_number(tok):
                marked.add(b)
            elif i > 0 and tok[:1].isupper() and not tokens[i - 1].endswith((".", "!", "?")):
                marked.add(b)  # a name, not a sentence start
            if b not in FUNCTION_WORDS and len(b) > 1 and not _is_number(tok):
                counts[b] += 1
    # topic words: recurring AND in the title ("bạch", "tuộc"), not just frequent ("nơi", "sống")
    title = {_bare(t) for t in script.title.split()}
    topic = [w for w, n in counts.most_common() if n >= 3 and w in title][:MAX_TOPIC_WORDS]
    return marked | set(topic)


def _closes_within(words: list[WordTiming], i: int, lookahead: int) -> bool:
    """True if a sentence/clause ends within the next `lookahead` words with no pause before it."""
    for j in range(i + 1, min(i + 1 + lookahead, len(words))):
        if words[j].start - words[j - 1].end > PAUSE_BREAK:
            return False
        if words[j].word.endswith(SENTENCE_PUNCT):
            return True
    return False


def _keeps_going(w: WordTiming, nxt: WordTiming | None) -> bool:
    """A chunk shouldn't end here: on a function word, or between a number and its unit."""
    if nxt is None or nxt.start - w.end > PAUSE_BREAK:
        return False
    return _bare(w.word) in FUNCTION_WORDS or _is_number(w.word)


def chunk_words(words: list[WordTiming], max_words: int, orphan_lookahead: int = 0,
                stretch: int = 2) -> list[list[WordTiming]]:
    """Break on punctuation and pauses; at max_words too, unless that would end on a function word or
    split a number from its unit (then up to `stretch` more words), or leave 1-`orphan_lookahead`
    words of the sentence alone on screen."""
    chunks: list[list[WordTiming]] = []
    current: list[WordTiming] = []
    for i, w in enumerate(words):
        current.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        pause = nxt is not None and nxt.start - w.end > PAUSE_BREAK
        over = len(current) >= max_words
        hold = (_keeps_going(w, nxt) and len(current) < max_words + stretch) or _closes_within(words, i, orphan_lookahead)
        if (over and not hold) or w.word.endswith(SENTENCE_PUNCT) or pause or nxt is None:
            chunks.append(current)
            current = []
    return chunks


def two_lines(tokens: list[str], max_chars: int = LONG_LINE_CHARS) -> str:
    """Split at the word boundary that best balances the two lines by character length."""
    if len(" ".join(tokens)) <= max_chars or len(tokens) < 2:
        return " ".join(tokens)
    total = len(" ".join(tokens))
    best = min(range(1, len(tokens)),
               key=lambda k: abs(len(" ".join(tokens[:k])) * 2 - total))
    return " ".join(tokens[:best]) + r"\N" + " ".join(tokens[best:])


def _chunk_end(chunk: list[WordTiming], next_chunk: list[WordTiming] | None) -> float:
    """Run until the next chunk starts (no flicker), unless there's a real pause."""
    if next_chunk and next_chunk[0].start - chunk[-1].end <= PAUSE_BREAK:
        return next_chunk[0].start
    return chunk[-1].end + HOLD_AFTER


def _events_short(chunks: list[list[WordTiming]], emphasis: set[str]) -> list[tuple[float, float, str]]:
    """One karaoke event per chunk: each word fills white→yellow over its own spoken duration."""
    events = []
    for ci, chunk in enumerate(chunks):
        end = _chunk_end(chunk, chunks[ci + 1] if ci + 1 < len(chunks) else None)
        parts = []
        for k, w in enumerate(chunk):
            until = chunk[k + 1].start if k + 1 < len(chunk) else w.end
            cs = max(1, round((until - w.start) * 100))
            word = _escape(w.word)
            if _bare(w.word) in emphasis:
                word = f"{EMPHASIS_ON}{word}{EMPHASIS_OFF}"
            parts.append(f"{{\\kf{cs}}}{word}")
        events.append((chunk[0].start, end, POP + " ".join(parts)))
    return events


def _events_long(chunks: list[list[WordTiming]], emphasis: set[str]) -> list[tuple[float, float, str]]:
    events = []
    for ci, chunk in enumerate(chunks):
        tokens = [_escape(w.word) for w in chunk]
        text = two_lines(tokens)
        for w in chunk:  # colour key words after line-splitting (tags don't count as characters)
            if _bare(w.word) in emphasis:
                esc = _escape(w.word)
                marked = f"{HIGHLIGHT}{esc}" + r"{\r}"
                # a function, not a replacement string: ASS tags like \c would be read as escapes
                text = re.sub(rf"(?<![\w{{]){re.escape(esc)}(?!\w)", lambda _m, s=marked: s, text, count=1)
        end = _chunk_end(chunk, chunks[ci + 1] if ci + 1 < len(chunks) else None)
        events.append((chunk[0].start, end, text))
    return events


def build_ass(script: Script, timeline: Timeline, preset: FormatPreset) -> str:
    fmt = script.format
    size, outline, shadow, margin_l, margin_r, margin_v = STYLES[fmt]
    words = display_words(script, timeline)
    emphasis = emphasis_words(script)
    if fmt == "short":
        events = _events_short(chunk_words(words, SHORT_MAX_WORDS, orphan_lookahead=1), emphasis)
        primary, secondary = YELLOW, WHITE  # karaoke: unsung (secondary) → sung (primary)
    else:
        events = _events_long(chunk_words(words, LONG_MAX_WORDS, orphan_lookahead=2), emphasis)
        primary, secondary = WHITE, WHITE

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {preset.width}
PlayResY: {preset.height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT},{size},{primary},{secondary},{BLACK},&H96000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_l},{margin_r},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Default,,0,0,0,,{t}" for s, e, t in events if e > s]
    return header + "\n".join(lines) + "\n"
