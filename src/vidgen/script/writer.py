"""Topic → validated Script. Short: one LLM call. Long: outline, then one call per chapter."""

import re
from pathlib import Path
from string import Template

from pydantic import BaseModel, Field

from vidgen.config import Format, FormatPreset, Lang
from vidgen.models import Scene, Script, SourceRef, VisualType
from vidgen.script.llm import LLMChain
from vidgen.script.research import Source, facts_block

PROMPTS = Path(__file__).parent / "prompts"
LANG_NAMES = {"vi": "Vietnamese", "en": "English"}
# Spoken rate of edge-tts neural voices; Vietnamese counts space-separated syllables.
WORDS_PER_SECOND = {"vi": 3.3, "en": 2.5}
MAX_SCENE_WORDS = 25
MIN_SCENE_WORDS = 5
SECONDS_PER_CHAPTER = 90
MIN_LENGTH_RATIO = 0.8  # below this share of the target word count, request one longer draft


# --- LLM response schemas (ids and chapter tags are assigned here, not by the model) ---
class LLMScene(BaseModel):
    narration: str
    visual_query: str
    visual_type: VisualType = "stock"
    ai_prompt: str = ""


class ShortDraft(BaseModel):
    title: str
    hook: str
    scenes: list[LLMScene] = Field(min_length=3)


class Chapter(BaseModel):
    title: str
    summary: str


class Outline(BaseModel):
    title: str
    hook: str
    hook_visual_query: str = Field(description="English stock footage query for the hook")
    chapters: list[Chapter] = Field(min_length=2)


class ChapterDraft(BaseModel):
    scenes: list[LLMScene] = Field(min_length=2)


def _render(name: str, **values) -> str:
    return Template((PROMPTS / name).read_text(encoding="utf-8")).substitute(**values)


def target_seconds(preset: FormatPreset) -> int:
    lo, hi = preset.target_seconds
    return (lo + hi) // 2


def generate(topic: str, fmt: Format, lang: Lang, preset: FormatPreset, llm: LLMChain,
             sources: list[Source] | None = None) -> Script:
    """`sources` are reference passages (see research.py); the prompts restrict facts to them."""
    sources = sources or []
    facts = facts_block(sources)
    seconds = target_seconds(preset)
    words = int(seconds * WORDS_PER_SECOND[lang])
    if fmt == "short":
        title, hook, scenes = _generate_short(topic, lang, preset, llm, seconds, words, facts)
    else:
        title, hook, scenes = _generate_long(topic, lang, preset, llm, seconds, words, facts)
    return Script(title=title, hook=hook, lang=lang, format=fmt,
                  scenes=postprocess(scenes, preset.max_ai_video),
                  sources=[SourceRef(title=s.title, url=s.url) for s in sources])


def _draft_words(draft) -> int:
    return sum(len(s.narration.split()) for s in draft.scenes)


def _generate_with_length(llm: LLMChain, prompt: str, schema, target_words: int):
    """Small local models under-write. If a draft is under MIN_LENGTH_RATIO of the target,
    ask once more with concrete feedback and keep the longer draft."""
    draft = llm.generate(prompt, schema)
    got = _draft_words(draft)
    if got >= target_words * MIN_LENGTH_RATIO:
        return draft
    feedback = (f"\n\nIMPORTANT: a previous draft had only {got} words, far too short. "
                f"Write at least {target_words} words: add more scenes with more concrete facts. "
                "Do not pad with filler or repeat sentences.")
    retry = llm.generate(prompt + feedback, schema)
    return retry if _draft_words(retry) > got else draft


def _generate_short(topic, lang, preset, llm, seconds, words, facts):
    prompt = _render(
        "short.md", topic=topic, facts=facts, lang_name=LANG_NAMES[lang], target_words=words,
        target_seconds=seconds, scene_range="8-14", max_ai_video=preset.max_ai_video,
    )
    draft = _generate_with_length(llm, prompt, ShortDraft, words)
    return draft.title, draft.hook, [Scene(id=0, **s.model_dump()) for s in draft.scenes]


def _generate_long(topic, lang, preset, llm, seconds, words, facts):
    n_chapters = max(3, round(seconds / SECONDS_PER_CHAPTER))
    outline = llm.generate(
        _render("long_outline.md", topic=topic, facts=facts, lang_name=LANG_NAMES[lang],
                target_minutes=round(seconds / 60), chapter_count=n_chapters),
        Outline,
    )
    outline_text = "\n".join(f"{i}. {c.title}: {c.summary}" for i, c in enumerate(outline.chapters, 1))
    per_chapter = (words - len(outline.hook.split())) // len(outline.chapters)

    scenes = [Scene(id=0, narration=outline.hook, visual_query=outline.hook_visual_query, chapter="Intro")]
    for i, ch in enumerate(outline.chapters, 1):
        if i == 1:
            note = "This chapter directly follows the intro hook; do not repeat it."
        elif i == len(outline.chapters):
            note = "This is the final chapter: wrap up the story and end with a question inviting comments."
        else:
            note = "Continue smoothly from the previous chapter; no greetings or recaps."
        draft = _generate_with_length(
            llm,
            _render("long_chapter.md", title=outline.title, facts=facts, outline=outline_text, chapter_index=i,
                    chapter_count=len(outline.chapters), chapter_title=ch.title,
                    chapter_summary=ch.summary, position_note=note, lang_name=LANG_NAMES[lang],
                    # spread the AI-video budget: one slot per chapter for the first N chapters
                    target_words=per_chapter, max_ai_video=1 if i <= preset.max_ai_video else 0),
            ChapterDraft, per_chapter,
        )
        scenes += [Scene(id=0, chapter=ch.title, **s.model_dump()) for s in draft.scenes]
    return outline.title, outline.hook, scenes


# --- post-processing -------------------------------------------------------------------
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_CLAUSE_END = re.compile(r"(?<=[,;:—–])\s+")


def _pieces(text: str, max_words: int) -> list[str]:
    """Break text into sentences; over-long sentences fall back to clauses, then raw word runs."""
    out: list[str] = []
    for sentence in filter(None, (s.strip() for s in _SENTENCE_END.split(text.strip()))):
        if len(sentence.split()) <= max_words:
            out.append(sentence)
            continue
        for clause in filter(None, (c.strip() for c in _CLAUSE_END.split(sentence))):
            words = clause.split()
            out += [" ".join(words[i:i + max_words]) for i in range(0, len(words), max_words)]
    return out


def split_narration(text: str, max_words: int = MAX_SCENE_WORDS) -> list[str]:
    """Greedily group sentence/clause pieces into chunks of at most max_words, then fold fragments
    shorter than MIN_SCENE_WORDS into a neighbour (a 2-word scene flashes by in under a second)."""
    chunks: list[str] = []
    current: list[str] = []
    for piece in _pieces(text, max_words):
        if current and len(" ".join(current + [piece]).split()) > max_words:
            chunks.append(" ".join(current))
            current = []
        current.append(piece)
    if current:
        chunks.append(" ".join(current))

    merged: list[str] = []
    carry = ""
    for chunk in chunks:
        chunk = f"{carry} {chunk}".strip() if carry else chunk
        carry = ""
        if len(chunk.split()) < MIN_SCENE_WORDS:
            carry = chunk
        else:
            merged.append(chunk)
    if carry:
        if merged:
            merged[-1] = f"{merged[-1]} {carry}"
        else:
            merged.append(carry)
    return merged


def postprocess(scenes: list[Scene], max_ai_video: int) -> list[Scene]:
    """Split long scenes, cap AI video count, fill missing AI prompts, renumber ids from 1."""
    out: list[Scene] = []
    ai_videos = 0
    for scene in scenes:
        if not scene.narration.strip():
            continue
        vtype = scene.visual_type
        if vtype == "ai_video":
            ai_videos += 1
            if ai_videos > max_ai_video:
                vtype = "ai_image"
        ai_prompt = scene.ai_prompt
        if vtype != "stock" and not ai_prompt:
            ai_prompt = f"photorealistic cinematic shot of {scene.visual_query}, natural lighting"
        for i, part in enumerate(split_narration(scene.narration)):
            out.append(scene.model_copy(update={
                "id": len(out) + 1,
                "narration": part,
                # only the first split part keeps the AI treatment; the rest use stock
                "visual_type": vtype if i == 0 else "stock",
                "ai_prompt": ai_prompt if i == 0 else "",
            }))
    return out
