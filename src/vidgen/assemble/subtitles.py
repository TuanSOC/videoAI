"""Word timings → ASS subtitles. Short: 1-3 big words with the spoken word highlighted. Long: 2-line sentence chunks."""

from vidgen.config import FormatPreset
from vidgen.models import Script, Timeline, WordTiming

FONT = "Be Vietnam Pro"
HIGHLIGHT = r"{\c&H0000FFFF&}"  # ASS colours are &HBBGGRR: yellow
RESET = r"{\r}"
SENTENCE_PUNCT = (".", "!", "?", "…", ",", ";", ":")
SHORT_MAX_WORDS = 3
LONG_MAX_WORDS = 12
PAUSE_BREAK = 0.3   # a gap this long between words starts a new chunk
HOLD_AFTER = 0.25   # keep the last chunk of a pause on screen a little longer

STYLES = {
    # fontsize, outline, shadow, margin_lr, margin_v are tuned for 1080x1920 / 1920x1080
    "short": (88, 6, 2, 80, 560),
    "long": (58, 3, 1, 160, 70),
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


def _closes_within(words: list[WordTiming], i: int, lookahead: int) -> bool:
    """True if a sentence/clause ends within the next `lookahead` words with no pause before it."""
    for j in range(i + 1, min(i + 1 + lookahead, len(words))):
        if words[j].start - words[j - 1].end > PAUSE_BREAK:
            return False
        if words[j].word.endswith(SENTENCE_PUNCT):
            return True
    return False


def chunk_words(words: list[WordTiming], max_words: int, orphan_lookahead: int = 0) -> list[list[WordTiming]]:
    """Break on punctuation, pauses and max_words — but stretch up to `orphan_lookahead` words
    past the cap rather than leave 1-2 words of a sentence alone on screen."""
    chunks: list[list[WordTiming]] = []
    current: list[WordTiming] = []
    for i, w in enumerate(words):
        current.append(w)
        nxt = words[i + 1] if i + 1 < len(words) else None
        pause = nxt is not None and nxt.start - w.end > PAUSE_BREAK
        full = len(current) >= max_words and not _closes_within(words, i, orphan_lookahead)
        if full or w.word.endswith(SENTENCE_PUNCT) or pause or nxt is None:
            chunks.append(current)
            current = []
    return chunks


def two_lines(tokens: list[str]) -> str:
    """Split at the word boundary that best balances the two lines by character length."""
    total = len(" ".join(tokens))
    best = min(range(1, len(tokens)),
               key=lambda k: abs(len(" ".join(tokens[:k])) * 2 - total))
    return " ".join(tokens[:best]) + r"\N" + " ".join(tokens[best:])


def _chunk_end(chunk: list[WordTiming], next_chunk: list[WordTiming] | None) -> float:
    """Run until the next chunk starts (no flicker), unless there's a real pause."""
    if next_chunk and next_chunk[0].start - chunk[-1].end <= PAUSE_BREAK:
        return next_chunk[0].start
    return chunk[-1].end + HOLD_AFTER


def _events_short(chunks: list[list[WordTiming]]) -> list[tuple[float, float, str]]:
    events = []
    for ci, chunk in enumerate(chunks):
        end = _chunk_end(chunk, chunks[ci + 1] if ci + 1 < len(chunks) else None)
        for k, w in enumerate(chunk):
            text = " ".join(
                f"{HIGHLIGHT}{_escape(x.word)}{RESET}" if j == k else _escape(x.word)
                for j, x in enumerate(chunk)
            )
            w_end = chunk[k + 1].start if k + 1 < len(chunk) else end
            events.append((w.start, w_end, text))
    return events


def _events_long(chunks: list[list[WordTiming]]) -> list[tuple[float, float, str]]:
    events = []
    for ci, chunk in enumerate(chunks):
        tokens = [_escape(w.word) for w in chunk]
        text = two_lines(tokens) if len(tokens) > LONG_MAX_WORDS // 2 else " ".join(tokens)
        end = _chunk_end(chunk, chunks[ci + 1] if ci + 1 < len(chunks) else None)
        events.append((chunk[0].start, end, text))
    return events


def build_ass(script: Script, timeline: Timeline, preset: FormatPreset) -> str:
    fmt = script.format
    size, outline, shadow, margin_lr, margin_v = STYLES[fmt]
    words = display_words(script, timeline)
    if fmt == "short":
        events = _events_short(chunk_words(words, SHORT_MAX_WORDS))
    else:
        events = _events_long(chunk_words(words, LONG_MAX_WORDS, orphan_lookahead=2))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {preset.width}
PlayResY: {preset.height}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,{outline},{shadow},2,{margin_lr},{margin_lr},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Default,,0,0,0,,{t}" for s, e, t in events if e > s]
    return header + "\n".join(lines) + "\n"
