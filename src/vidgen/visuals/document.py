"""Evidence scenes: the source sentence behind a figure, on a paper card, with a highlighter sweeping
over the figure — instead of yet another stock clip for "150,000 cameras were accessed".

Honesty rules, enforced here:
- the text is a REAL sentence from the research sources (never the narration, never invented); a scene
  whose figure no source sentence contains gets no card;
- a plain paper card with "From the source: <article>" — no masthead, logo, byline or date: nothing
  that could pass for a real publication.

The sweep is drawn frame by frame with Pillow (FFmpeg's drawbox evaluates its width once, so it can't
animate), then held; the result is an ordinary video clip for the renderer (source "document").
"""

import logging
import re
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from vidgen import ffmpeg
from vidgen.config import ROOT
from vidgen.models import Script, SourceDoc
from vidgen.text import CLAUSE_END, bare, ends_with

log = logging.getLogger(__name__)
FONT = ROOT / "assets" / "fonts" / "BeVietnamPro-Bold.ttf"
LIMIT = {"short": 2, "long": 4}   # cards per video: more reads as a template
MIN_GAP_SCENES = 3
MAX_QUOTE_WORDS = 40
PHRASE_WORDS = 3                  # the figure and the words right after it ("150.000 camera an")
FPS = 30
LEAD, SWEEP = 0.4, 0.6            # seconds before the highlighter starts, and its sweep
PAPER, INK, MUTED = (243, 238, 227), (30, 30, 30), (125, 112, 95)
MARK = (255, 212, 0, 150)         # highlighter yellow, ~60 % opaque
LABEL = {"vi": ("TRÍCH TỪ NGUỒN", "Nguồn"), "en": ("FROM THE SOURCE", "Source")}
_SENTENCE = re.compile(r"(?<=[.!?…])\s+")


@dataclass(frozen=True)
class Quote:
    text: str           # the source sentence, verbatim
    start: int          # highlighted words: text.split()[start:end]
    end: int
    source_title: str
    url: str
    lang: str


def _digits(token: str) -> str:
    return re.sub(r"\D", "", token)


def _figures(narration: str) -> set[str]:
    """Figures worth showing: 2+ digits or a percentage ("3" alone matches far too many sentences)."""
    return {d for t in narration.split() if (d := _digits(t)) and (len(d) >= 2 or "%" in t)}


def _compounds(sentence: str) -> set[tuple[str, str]]:
    from vidgen.assemble.subtitles import _vi_tokenizer

    tokenize = _vi_tokenizer()
    if tokenize is None:
        return set()
    try:
        units = tokenize(sentence).split()
    except Exception:
        return set()
    out = set()
    for unit in units:
        syllables = [bare(x) for x in unit.split("_")]
        out |= set(zip(syllables, syllables[1:]))
    return out


def find_quote(narration: str, sources: list[SourceDoc], lang: str) -> Quote | None:
    """The shortest source sentence containing one of the narration's figures (same-language sources
    first; "150.000" and "150,000" are the same figure), or None."""
    figures = _figures(narration)
    if not figures:
        return None
    for src in sorted(sources, key=lambda s: s.lang != lang):
        best: Quote | None = None
        for sentence in _SENTENCE.split(src.text):
            words = sentence.split()
            if not 4 <= len(words) <= MAX_QUOTE_WORDS:
                continue
            for i, w in enumerate(words):
                if _digits(w) in figures:
                    end = i + 1
                    while end < len(words) and end - i < PHRASE_WORDS and not ends_with(words[end - 1], CLAUSE_END):
                        end += 1
                    # never stop inside a Vietnamese compound ("camera an | ninh")
                    glue = _compounds(sentence) if src.lang == "vi" else set()
                    if (end < len(words) and not ends_with(words[end - 1], CLAUSE_END)
                            and (bare(words[end - 1]), bare(words[end])) in glue):
                        end += 1
                    if best is None or len(words) < len(best.text.split()):
                        best = Quote(" ".join(words), i, end, src.title, src.url, src.lang)
                    break
        if best is not None:
            return best
    return None


def plan_documents(script: Script, sources: list[SourceDoc], limit: int) -> dict[int, Quote]:
    """Scene id → quote, for at most `limit` scenes, never the hook or the closing scene, spread out."""
    chosen: dict[int, Quote] = {}
    last = -MIN_GAP_SCENES
    for i, scene in enumerate(script.scenes[1:-1], 1):
        if len(chosen) >= limit or i - last < MIN_GAP_SCENES:
            continue
        quote = find_quote(scene.narration, sources, script.lang)
        if quote is not None:
            chosen[scene.id] = quote
            last = i
    return chosen


# --- drawing -----------------------------------------------------------------------------------------------
@dataclass
class Layout:
    size: tuple[int, int]
    font_size: int
    words: list[tuple[int, int, int, int]]   # x, y, w, h of every word of the quote
    boxes: list[tuple[int, int, int, int]]   # highlighter rectangles over the phrase, one per line
    label_y: int
    source_y: int


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT), size)


def layout(quote: Quote, size: tuple[int, int]) -> Layout:
    """Word positions for the largest font that fits between the top and the caption safe zone."""
    w, h = size
    portrait = h > w
    side = round(w * 0.1)
    top = round(h * (0.14 if portrait else 0.12))
    bottom = h - round(h * (640 / 1920 if portrait else 160 / 1080))   # captions live below this
    words = quote.text.split()
    size_px = round(w * (0.062 if portrait else 0.034))
    while True:
        font = _font(size_px)
        line_h = round(size_px * 1.38)
        space = font.getlength(" ")
        placed, x, y = [], side, 0
        for word in words:
            ww = round(font.getlength(word))
            if x > side and x + ww > w - side:
                x, y = side, y + line_h
            placed.append((x, y, ww, round(size_px * 1.2)))
            x += ww + space
        block = y + line_h + round(size_px * 2.2)          # + label above and source line below
        if top + block <= bottom or size_px <= 24:
            break
        size_px -= 2
    offset = top + (bottom - top - block) // 3 + round(size_px * 1.2)   # a little above centre
    placed = [(round(x), y + offset, ww, hh) for x, y, ww, hh in placed]
    boxes: list[tuple[int, int, int, int]] = []
    pad = round(size_px * 0.12)
    for x, y, ww, hh in placed[quote.start:quote.end]:
        if boxes and boxes[-1][1] == y - pad:           # same line: extend
            bx, by, bw, bh = boxes[-1]
            boxes[-1] = (bx, by, x + ww + pad - bx, bh)
        else:
            boxes.append((x - pad, y - pad, ww + 2 * pad, hh + pad))
    return Layout(size, size_px, placed, boxes, offset - round(size_px * 1.2),
                  placed[-1][1] + round(size_px * 1.9))


def _paper(size: tuple[int, int]) -> Image.Image:
    rng = np.random.default_rng(7)
    w, h = size
    base = np.empty((h, w, 3), np.float32)
    base[:] = PAPER
    base += rng.normal(0, 3.0, (h, w, 1))                         # paper grain
    yy, xx = np.mgrid[0:h, 0:w]
    r = np.hypot((xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2))
    base *= (1 - 0.10 * np.clip(r - 0.4, 0, 1))[..., None]       # soft vignette
    return Image.fromarray(np.clip(base, 0, 255).astype(np.uint8)).convert("RGBA")


def frame(quote: Quote, lay: Layout, paper: Image.Image, progress: float) -> Image.Image:
    """The card with the highlighter `progress` (0..1) of the way through the phrase."""
    img = paper.copy()
    total = sum(b[2] for b in lay.boxes)
    left = progress * total
    marks = Image.new("RGBA", lay.size, (0, 0, 0, 0))
    draw_marks = ImageDraw.Draw(marks)
    for x, y, bw, bh in lay.boxes:                                 # sweep line by line
        width = min(bw, left)
        if width > 0:
            draw_marks.rounded_rectangle((x, y, x + width, y + bh), radius=round(bh * 0.18), fill=MARK)
        left -= bw
    img = Image.alpha_composite(img, marks)
    draw = ImageDraw.Draw(img)
    font, small = _font(lay.font_size), _font(max(18, round(lay.font_size * 0.5)))
    label, source = LABEL.get(quote.lang, LABEL["en"])
    side = lay.words[0][0]
    draw.text((side, lay.label_y), label, font=small, fill=MUTED)
    for (x, y, _, _), word in zip(lay.words, quote.text.split()):
        draw.text((x, y), word, font=font, fill=INK)
    draw.text((side, lay.source_y), f"{source}: Wikipedia — {quote.source_title}", font=small, fill=MUTED)
    return img.convert("RGB")


def make_clip(quote: Quote, size: tuple[int, int], seconds: float, out: Path) -> Path:
    """An mp4 `seconds` long: the card, the highlighter sweeping in after LEAD, then held."""
    lay = layout(quote, size)
    paper = _paper(size)
    lead, sweep = round(LEAD * FPS), round(SWEEP * FPS)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        frames = [0.0] * lead + [(i + 1) / sweep for i in range(sweep)]
        for k, p in enumerate(frames):
            frame(quote, lay, paper, p).save(Path(tmp) / f"f{k:04d}.png")
        hold = max(0.0, seconds - len(frames) / FPS)
        ffmpeg.run(["-framerate", str(FPS), "-i", str(Path(tmp) / "f%04d.png"),
                    "-vf", f"tpad=stop_mode=clone:stop_duration={hold:.3f},format=yuv420p",
                    "-t", f"{seconds:.3f}", "-r", str(FPS), *ffmpeg.video_encoder(), str(out)])
    return out
