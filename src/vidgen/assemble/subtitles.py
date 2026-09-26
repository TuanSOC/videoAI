"""Word timings → ASS subtitles.

Short: one line of 3-6 words, white with a black outline; the word being spoken turns yellow (\\k).
No pop-in or zoomed keywords: they read as busy. Long: 2-line chunks balanced by characters, key words
(numbers, names, the video's recurring topic words) coloured. Chunks follow meaning: never end on a function word, keep a number with its
unit, don't strand one word before a full stop. Positions keep clear of TikTok/Shorts/Reels UI.
"""

import logging
import warnings
from collections import Counter

from vidgen.config import FormatPreset
from vidgen.models import Script, Timeline, WordTiming
from vidgen.text import CLAUSE_END, SENTENCE_END, align, bare, ends_with, is_number

log = logging.getLogger(__name__)
FONT = "Be Vietnam Pro"
# ASS colours are &HAABBGGRR
WHITE, YELLOW, BLACK = "&H00FFFFFF", "&H0000E5FF", "&H00000000"
HIGHLIGHT = r"{\c&H0000E5FF&}"  # long-format emphasis colour (yellow)
SENTENCE_PUNCT = CLAUSE_END
SHORT_MAX_WORDS = 6
SHORT_MIN_WORDS = 3
SHORT_IDEAL_WORDS = 4
SHORT_MAX_CHARS = 26   # one line at the style's font size
SHORT_HARD_CHARS = 30  # allowed (with a cost) when long words leave no better split; drawn smaller
LONG_MAX_WORDS = 12
LONG_LINE_CHARS = 42
PAUSE_BREAK = 0.3   # a gap this long between words starts a new chunk
HOLD_AFTER = 0.25   # keep the last chunk of a pause on screen a little longer
MAX_TOPIC_WORDS = 4

# words a chunk must not end on (the listener expects what follows)
FUNCTION_WORDS = set("""
của và là những các một với cho trong để khi thì mà nhưng hay hoặc đã sẽ đang được bị có không
rất cũng này đó nào như từ vào ra lên xuống tại về theo bởi vì nên nếu còn chỉ mỗi mọi
lại rồi nữa luôn thêm vẫn đều hơn nhất quá đi
the a an of to in on at by for with from and or but is are was were be been that this these those
its their his her our your my as into than then so if when while which who whose
until unless because since after before about over under through without within via per like
not no nor yet can will would could should may might must has have had do does did
""".split())

STYLES = {
    # fontsize, outline, shadow, margin_l, margin_r, margin_v — tuned for 1080x1920 / 1920x1080.
    # short: right margin wider than left to clear the like/comment/share column; margin_v keeps the
    # text above the caption/music strip at the bottom of TikTok/Shorts/Reels.
    "short": (76, 6, 3, 110, 210, 620),  # 26 chars at 76px ≈ 730px: fits one line between margins
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


def display_words(script: Script, timeline: Timeline) -> list[WordTiming]:
    """TTS word events drop punctuation (and may merge "năm 2013," into one event): restore the text as
    written by pairing tokens with events (text.align). A scene that can't be paired keeps the events."""
    narration = {s.id: s.narration.split() for s in script.scenes}
    out: list[WordTiming] = []
    for sa in timeline.scenes:
        tokens = narration.get(sa.scene_id, [])
        groups = align(tokens, [w.word for w in sa.words]) if tokens and sa.words else None
        if groups is None:
            out += sa.words
            continue
        for token_ids, word_ids in groups:
            first, last = sa.words[word_ids[0]], sa.words[word_ids[-1]]
            out.append(WordTiming(word=" ".join(tokens[t] for t in token_ids), start=first.start, end=last.end))
    return out


def emphasis_words(script: Script) -> set[str]:
    """Bare words to emphasise: numbers, capitalised names mid-sentence, and the few content words
    the narration keeps coming back to (the topic: "bạch", "tuộc")."""
    marked: set[str] = set()
    counts: Counter[str] = Counter()
    for scene in script.scenes:
        tokens = scene.narration.split()
        for i, tok in enumerate(tokens):
            b = bare(tok)
            if not b:
                continue
            if is_number(tok):
                marked.add(b)
            elif i > 0 and tok[:1].isupper() and not ends_with(tokens[i - 1]):
                marked.add(b)  # a name, not a sentence start
            if b not in FUNCTION_WORDS and len(b) > 1 and not is_number(tok):
                counts[b] += 1
    # topic words: recurring AND in the title ("bạch", "tuộc"), not just frequent ("nơi", "sống")
    title = {bare(t) for t in script.title.split()}
    topic = [w for w, n in counts.most_common() if n >= 3 and w in title][:MAX_TOPIC_WORDS]
    return marked | set(topic)


def _closes_within(words: list[WordTiming], i: int, lookahead: int) -> bool:
    """True if a sentence/clause ends within the next `lookahead` words with no pause before it."""
    for j in range(i + 1, min(i + 1 + lookahead, len(words))):
        if words[j].start - words[j - 1].end > PAUSE_BREAK:
            return False
        if ends_with(words[j].word, SENTENCE_PUNCT):
            return True
    return False


def _keeps_going(w: WordTiming, nxt: WordTiming | None) -> bool:
    """A chunk shouldn't end here: on a function word, or between a number and its unit."""
    if nxt is None or nxt.start - w.end > PAUSE_BREAK:
        return False
    return bare(w.word) in FUNCTION_WORDS or is_number(w.word)


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
        if (over and not hold) or ends_with(w.word, SENTENCE_PUNCT) or pause or nxt is None:
            chunks.append(current)
            current = []
    return chunks


def _segments(words: list[WordTiming]) -> list[list[WordTiming]]:
    """Split at sentence ends and real pauses; a chunk never spans those."""
    out: list[list[WordTiming]] = []
    current: list[WordTiming] = []
    for i, w in enumerate(words):
        current.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        if nxt is None or ends_with(w.word, SENTENCE_END) or nxt.start - w.end > PAUSE_BREAK:
            out.append(current)
            current = []
    return out


def _chunk_cost(chunk: list[WordTiming], closes_segment: bool) -> float:
    n, chars = len(chunk), len(" ".join(w.word for w in chunk))
    if n > SHORT_MAX_WORDS or (chars > SHORT_HARD_CHARS and n > 1):
        return float("inf")
    cost = abs(n - SHORT_IDEAL_WORDS) + 3 * max(0, chars - SHORT_MAX_CHARS)
    if n < SHORT_MIN_WORDS:
        # only when the sentence leaves no better split; a lone word is the worst (it flashes by)
        cost += 6 if n == 2 else 16
    last = chunk[-1].word
    if not closes_segment:
        if bare(last) in FUNCTION_WORDS or is_number(last):
            cost += 14  # "để" / "and" / "3" at a line end: the viewer waits for the rest
        if ends_with(last, SENTENCE_PUNCT):
            cost -= 2  # a comma is a natural place to break
    return cost


def compound_pairs(script: Script) -> set[tuple[str, str]]:
    """Syllable pairs that form one Vietnamese word ("tài khoản", "mật khẩu"): Vietnamese writes compound
    words with a space, so a phrase break must not fall between them. From a word segmenter (underthesea), plus
    pairs the narration keeps repeating (a topic word the segmenter doesn't know)."""
    if script.lang != "vi":
        return set()
    pairs: Counter[tuple[str, str]] = Counter()
    for scene in script.scenes:
        tokens = scene.narration.split()
        for a, b in zip(tokens, tokens[1:]):
            if not ends_with(a, SENTENCE_PUNCT) and bare(a) not in FUNCTION_WORDS and bare(b) not in FUNCTION_WORDS:
                pairs[(bare(a), bare(b))] += 1
    glue = {p for p, n in pairs.items() if n >= 2}
    tokenize = _vi_tokenizer()
    for scene in script.scenes if tokenize else ():
        try:
            segmented = tokenize(scene.narration)
        except Exception as e:  # a broken segmenter must not fail the render: repeated pairs still apply
            log.warning("Vietnamese word segmentation failed: %s", e)
            break
        for word in segmented.split():
            syllables = [bare(s) for s in word.split("_")]
            glue |= set(zip(syllables, syllables[1:]))
    return glue


def _vi_tokenizer():
    try:
        with warnings.catch_warnings():  # the segmenter's dependencies are noisy on first import
            warnings.simplefilter("ignore")
            from underthesea import word_tokenize
    except Exception as e:  # not installed, or its model fails to load
        log.warning("Vietnamese word segmenter unavailable (%s): compound words may be split", e)
        return None
    return lambda text: word_tokenize(text, format="text")


def short_chunks(words: list[WordTiming], glue: set[tuple[str, str]] = frozenset()) -> list[list[WordTiming]]:
    """Best split of each sentence into one-line phrases: 3-6 words, ≤ SHORT_MAX_CHARS, not ending on a
    function word or a bare number, not splitting a `glue` pair, preferring commas and ~4 words
    (dynamic programming, not greedy, so the last phrase of a sentence is never a lone word)."""
    chunks: list[list[WordTiming]] = []
    for seg in _segments(words):
        n = len(seg)
        best: list[tuple[float, int]] = [(0.0, 0)] + [(float("inf"), 0)] * n  # (cost, previous cut)
        for end in range(1, n + 1):
            split_word = end < n and (bare(seg[end - 1].word), bare(seg[end].word)) in glue
            for start in range(max(0, end - SHORT_MAX_WORDS), end):
                c = best[start][0] + _chunk_cost(seg[start:end], end == n) + (8 if split_word else 0)
                if c < best[end][0]:
                    best[end] = (c, start)
        if best[n][0] == float("inf"):  # one word longer than a line: give it a line of its own
            chunks += [[w] for w in seg]
            continue
        cuts, end = [], n
        while end > 0:
            cuts.append(end)
            end = best[end][1]
        start = 0
        for end in reversed(cuts):
            chunks.append(seg[start:end])
            start = end
    return chunks


def _line_break(tokens: list[str], max_chars: int = LONG_LINE_CHARS) -> int | None:
    """Index of the first token of line 2 (the most balanced split by characters), or None: one line."""
    total = len(" ".join(tokens))
    if total <= max_chars or len(tokens) < 2:
        return None
    return min(range(1, len(tokens)), key=lambda k: abs(len(" ".join(tokens[:k])) * 2 - total))


def two_lines(tokens: list[str], max_chars: int = LONG_LINE_CHARS, shown: list[str] | None = None) -> str:
    """Split at the word boundary that best balances the two lines by character length. `shown`: the
    same tokens with ASS tags added (tags must not count as characters)."""
    shown = shown or tokens
    k = _line_break(tokens, max_chars)
    return " ".join(shown) if k is None else " ".join(shown[:k]) + r"\N" + " ".join(shown[k:])


def _chunk_end(chunk: list[WordTiming], next_chunk: list[WordTiming] | None) -> float:
    """Run until the next chunk starts (no flicker), unless there's a real pause."""
    if next_chunk and next_chunk[0].start - chunk[-1].end <= PAUSE_BREAK:
        return next_chunk[0].start
    return chunk[-1].end + HOLD_AFTER


def _events_short(chunks: list[list[WordTiming]], size: int) -> list[tuple[float, float, str]]:
    """One event per phrase; each word switches white→yellow when it is spoken (karaoke \\k)."""
    events = []
    for ci, chunk in enumerate(chunks):
        end = _chunk_end(chunk, chunks[ci + 1] if ci + 1 < len(chunks) else None)
        parts = []
        for k, w in enumerate(chunk):
            until = chunk[k + 1].start if k + 1 < len(chunk) else w.end
            parts.append(f"{{\\k{max(1, round((until - w.start) * 100))}}}{_escape(w.word)}")
        chars = len(" ".join(w.word for w in chunk))
        # a line longer than SHORT_MAX_CHARS (long words) is drawn smaller so it stays on one line
        fit = f"{{\\fs{size * SHORT_MAX_CHARS // chars}}}" if chars > SHORT_MAX_CHARS else ""
        events.append((chunk[0].start, end, fit + " ".join(parts)))
    return events


def _events_long(chunks: list[list[WordTiming]], emphasis: set[str]) -> list[tuple[float, float, str]]:
    events = []
    for ci, chunk in enumerate(chunks):
        tokens = [_escape(w.word) for w in chunk]
        # colour key words per token (matching the joined text missed a word right after "\N")
        shown = [f"{HIGHLIGHT}{t}" + r"{\r}" if bare(w.word) in emphasis else t for t, w in zip(tokens, chunk)]
        text = two_lines(tokens, shown=shown)
        end = _chunk_end(chunk, chunks[ci + 1] if ci + 1 < len(chunks) else None)
        events.append((chunk[0].start, end, text))
    return events


def build_ass(script: Script, timeline: Timeline, preset: FormatPreset) -> str:
    fmt = script.format
    size, outline, shadow, margin_l, margin_r, margin_v = STYLES[fmt]
    words = display_words(script, timeline)
    if fmt == "short":
        events = _events_short(short_chunks(words, compound_pairs(script)), size)
        primary, secondary = YELLOW, WHITE  # karaoke: unsung (secondary) → sung (primary)
    else:
        events = _events_long(chunk_words(words, LONG_MAX_WORDS, orphan_lookahead=2), emphasis_words(script))
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
