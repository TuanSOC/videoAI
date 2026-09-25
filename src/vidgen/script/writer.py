"""Topic → validated Script. Short: one LLM call. Long: outline, then one call per chapter."""

import json
import logging
import re
from itertools import groupby
from pathlib import Path
from string import Template

from pydantic import BaseModel, Field

from vidgen.config import Format, FormatPreset, Lang
from vidgen.models import Angle, Scene, Script, SourceRef, VisualType
from vidgen.script.llm import LLMChain
from vidgen.script.research import Source, facts_block

log = logging.getLogger(__name__)
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


def angle_block(angle: Angle | None) -> str:
    if angle is None:
        return "No angle was chosen: pick the most interesting one supported by the reference material."
    points = "\n".join(f"- {p}" for p in angle.key_points)
    return (f"Angle chosen by the editor (follow it):\n"
            f"Title: {angle.title}\n"
            f"Opening hook (use as the first sentence; light polishing allowed): {angle.hook}\n"
            f"Cover these points, in this order:\n{points}")


def generate(topic: str, fmt: Format, lang: Lang, preset: FormatPreset, llm: LLMChain,
             sources: list[Source] | None = None, angle: Angle | None = None) -> Script:
    """`sources` are reference passages (see research.py); the prompts restrict facts to them.
    `angle` is the brief the user picked: its title is kept verbatim, its hook and points steer the script."""
    sources = sources or []
    ctx = {"facts": facts_block(sources), "angle": angle_block(angle)}
    seconds = target_seconds(preset)
    words = int(seconds * WORDS_PER_SECOND[lang])
    if fmt == "short":
        title, hook, scenes = _generate_short(topic, lang, preset, llm, seconds, words, ctx)
    else:
        title, hook, scenes = _generate_long(topic, lang, preset, llm, seconds, words, ctx)
    if angle is not None:
        title = angle.title
    return Script(title=title, hook=hook, lang=lang, format=fmt,
                  scenes=postprocess(scenes, preset.max_ai_video),
                  sources=[SourceRef(title=s.title, url=s.url) for s in sources])


def _draft_words(draft) -> int:
    return sum(len(s.narration.split()) for s in draft.scenes)


class ExpandedScenes(BaseModel):
    scenes: list[LLMScene] = Field(min_length=1)


WORDS_PER_NEW_SCENE = 15


def expand_scenes(scenes: list[LLMScene], target_words: int, ctx: dict, lang: str,
                  llm: LLMChain, pin_last: bool = True) -> list[LLMScene] | None:
    """Grow a draft toward target_words by INSERTING scenes built from unused facts.
    Small local models ignore "write longer" when rewriting from scratch (real drafts stayed at
    117-164 words for a ~200 target); extending a draft they can see works far better.
    Returns None if the result isn't longer or didn't keep the original scenes."""
    current = sum(len(s.narration.split()) for s in scenes)
    if current >= target_words:
        return None
    listing = json.dumps([{"narration": s.narration, "visual_query": s.visual_query} for s in scenes],
                         ensure_ascii=False, indent=1)
    new_count = max(1, round((target_words - current) / WORDS_PER_NEW_SCENE))
    try:
        result = llm.generate(_render("expand.md", scenes=listing, current_words=current,
                                      target_words=target_words, new_scenes=new_count,
                                      lang_name=LANG_NAMES[lang], **ctx), ExpandedScenes).scenes
    except Exception as e:  # LLM trouble just means "keep the draft"
        log.warning("expanding the script failed: %s", e)
        return None
    grew = sum(len(s.narration.split()) for s in result) > current
    return result if grew and _keeps_originals(scenes, result, pin_last) else None


def _keeps_originals(original: list[LLMScene], result: list[LLMScene], pin_last: bool = True) -> bool:
    """Every original scene is still there exactly once, in the same order, the first one still first
    and (pin_last: the video's closing question) the last one still last. A set/percentage check let
    reorders, drops and duplicates through."""
    new = [s.narration.strip() for s in result]
    old = [s.narration.strip() for s in original]
    if not old or new[0] != old[0] or (pin_last and new[-1] != old[-1]):
        return False
    if any(new.count(t) != old.count(t) for t in set(old)):
        return False
    pos = 0
    for text in old:  # subsequence check
        try:
            pos = new.index(text, pos) + 1
        except ValueError:
            return False
    return True


def _generate_with_length(llm: LLMChain, prompt: str, schema, target_words: int, ctx: dict, lang: str):
    """Draft, then if it's under MIN_LENGTH_RATIO of the target, expand it (see expand_scenes)."""
    draft = llm.generate(prompt, schema)
    if _draft_words(draft) >= target_words * MIN_LENGTH_RATIO:
        return draft
    expanded = expand_scenes(draft.scenes, target_words, ctx, lang, llm)
    return draft.model_copy(update={"scenes": expanded}) if expanded else draft


DEFAULT_REWRITE = "Make it more engaging and clearer, same meaning, stay factual."


SHORTER_HINTS = ("ngắn", "gọn", "short", "concise", "brief")


def rewrite_one(script: Script, scene: Scene, prev: str, nxt: str, instruction: str | None,
                sources: list[Source], angle: Angle | None, llm: LLMChain) -> LLMScene:
    """One scene rewritten. An 8B model tends to merge the neighbouring scene in (seen live: "shorter"
    returned 32 words vs 18), so a result over the limit — or not shorter when asked — gets one retry."""
    instr = (instruction or "").strip() or DEFAULT_REWRITE
    current_words = len(scene.narration.split())
    wants_shorter = any(h in instr.lower() for h in SHORTER_HINTS)
    limit = min(MAX_SCENE_WORDS, current_words - 1) if wants_shorter and current_words > 3 else MAX_SCENE_WORDS
    prompt = _render(
        "rewrite_scene.md", title=script.title, lang_name=LANG_NAMES[script.lang], angle=angle_block(angle),
        facts=facts_block(sources), prev=prev or "(none — this is the opening)", current=scene.narration,
        next=nxt or "(none — this is the ending)", instruction=instr, max_words=limit,
        current_words=current_words)
    out = llm.generate(prompt, LLMScene)
    got = len(out.narration.split())
    if got > limit:
        retry = llm.generate(prompt + f"\n\nYour previous answer had {got} words: rewrite it with at most "
                                      f"{limit} words, this scene's idea only.", LLMScene)
        if len(retry.narration.split()) < got:
            out = retry
    if not out.visual_query.isascii():  # seen live: a Vietnamese query, useless for stock search
        out = out.model_copy(update={"visual_query": scene.visual_query})
    return out


def extend_script(script: Script, preset: FormatPreset, llm: LLMChain, sources: list[Source] | None = None,
                  angle: Angle | None = None) -> Script | None:
    """"Kéo dài" button: bring an existing script toward the middle of the format's target length.
    Long videos are extended chapter by chapter (a 100-scene prompt would not fit the context);
    the deficit is shared in proportion to each chapter's length. None if nothing could be added."""
    ctx = {"facts": facts_block(sources or []), "angle": angle_block(angle)}
    target = int(target_seconds(preset) * WORDS_PER_SECOND[script.lang])
    total = script.word_count
    if total == 0 or total >= target:
        return None
    out: list[Scene] = []
    changed = False
    # consecutive runs only: a scene moved across a chapter border must not be pulled back into it
    for chapter, run in groupby(script.scenes, key=lambda sc: sc.chapter):
        scenes = list(run)
        words = sum(len(s.narration.split()) for s in scenes)
        share = (target - total) * words / total
        grown = None
        if chapter != "Intro" and share >= WORDS_PER_NEW_SCENE / 2:
            drafts = [LLMScene(narration=s.narration, visual_query=s.visual_query, visual_type=s.visual_type,
                               ai_prompt=s.ai_prompt) for s in scenes]
            # only the last chapter holds the closing question that must stay last
            grown = expand_scenes(drafts, int(words + share), ctx, script.lang, llm,
                                  pin_last=scenes[-1] is script.scenes[-1])
        if grown:
            changed = True
            out += [Scene(id=0, chapter=chapter, **s.model_dump()) for s in grown]
        else:
            out += scenes
    if not changed:
        return None
    return script.model_copy(update={"scenes": postprocess(out, preset.max_ai_video)})


def _generate_short(topic, lang, preset, llm, seconds, words, ctx):
    prompt = _render(
        "short.md", topic=topic, **ctx, lang_name=LANG_NAMES[lang], target_words=words,
        target_seconds=seconds, scene_range="8-14", max_ai_video=preset.max_ai_video,
    )
    draft = _generate_with_length(llm, prompt, ShortDraft, words, ctx, lang)
    return draft.title, draft.hook, [Scene(id=0, **s.model_dump()) for s in draft.scenes]


def _generate_long(topic, lang, preset, llm, seconds, words, ctx):
    n_chapters = max(3, round(seconds / SECONDS_PER_CHAPTER))
    outline = llm.generate(
        _render("long_outline.md", topic=topic, **ctx, lang_name=LANG_NAMES[lang],
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
            _render("long_chapter.md", title=outline.title, **ctx, outline=outline_text, chapter_index=i,
                    chapter_count=len(outline.chapters), chapter_title=ch.title,
                    chapter_summary=ch.summary, position_note=note, lang_name=LANG_NAMES[lang],
                    # spread the AI-video budget: one slot per chapter for the first N chapters
                    target_words=per_chapter, max_ai_video=1 if i <= preset.max_ai_video else 0),
            ChapterDraft, per_chapter, ctx, lang,
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
