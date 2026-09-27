"""Topic → validated Script. Short: one LLM call. Long: outline, then one call per chapter."""

import logging
import re
from itertools import groupby

from pydantic import BaseModel, Field

from vidgen.assemble.music import MOODS
from vidgen.config import LANG_NAMES, Format, FormatPreset, Lang
from vidgen.models import Angle, Scene, Script, SourceRef, VisualType
from vidgen.script import hooks
from vidgen.text import bare, ends_with
from vidgen.script import templates
from vidgen.script.llm import LLMChain, LLMError
from vidgen.script.research import Source, facts_block

log = logging.getLogger(__name__)
# Spoken rate of edge-tts neural voices; Vietnamese counts space-separated syllables.
# spoken pace incl. pauses at voice_rate +8% with tight scene cuts, measured on shorts:
# vi 3.98 w/s (was 3.23 before the pacing changes), en 2.53 w/s (AndrewMultilingual)
WORDS_PER_SECOND = {"vi": 3.9, "en": 2.5}
MAX_SCENE_WORDS = 25
MIN_SCENE_WORDS = 5
SECONDS_PER_CHAPTER = 90
MAX_ALT_QUERIES = 2
MIN_LENGTH_RATIO = 0.8  # below this share of the target word count, request one longer draft


# --- LLM response schemas (ids and chapter tags are assigned here, not by the model) ---
class LLMScene(BaseModel):
    narration: str
    visual_query: str
    alt_queries: list[str] = []
    visual_type: VisualType = "stock"
    ai_prompt: str = ""


class ShortDraft(BaseModel):
    title: str
    hook: str
    mood: str = ""
    # required (no default): Ollama's structured output then forces the model to fill them — as
    # optional fields the 8B model simply left them out
    open_loop: str     # the question scene 2 raises...
    payoff_scene: int  # ...and the scene (1-based) that answers it, near the end
    scenes: list[LLMScene] = Field(min_length=3)


class HookFix(BaseModel):
    hook: str


class Chapter(BaseModel):
    title: str
    summary: str


class Outline(BaseModel):
    title: str
    hook: str
    hook_visual_query: str = Field(description="English stock footage query for the hook")
    mood: str = ""
    chapters: list[Chapter] = Field(min_length=2)


class ChapterDraft(BaseModel):
    scenes: list[LLMScene] = Field(min_length=1)  # postprocess splits a long one; 2 made valid drafts fail


def target_seconds(preset: FormatPreset) -> int:
    lo, hi = preset.target_seconds
    return (lo + hi) // 2


def angle_block(angle: Angle | None, opening: bool = True) -> str:
    """The editor's angle for a prompt. `opening` only for prompts that write the video's opening (the
    short draft, the long outline): given to a chapter, an expansion or a one-scene rewrite, "use the
    hook as the first sentence, cover every point" pulled the model into repeating the hook and merging
    other scenes' ideas."""
    if angle is None:
        return "No angle was chosen: pick the most interesting one supported by the reference material."
    points = "\n".join(f"- {p}" for p in angle.key_points)
    if not opening:
        return (f"Angle of the whole video (for context; this part covers only its own share):\n"
                f"Title: {angle.title}\nPoints of the whole video:\n{points}")
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
    body = {**ctx, "angle": angle_block(angle, opening=False)}  # chapters, expansions
    seconds = target_seconds(preset)
    words = int(seconds * WORDS_PER_SECOND[lang])
    if fmt == "short":
        title, hook, mood, scenes = _generate_short(topic, lang, preset, llm, seconds, words, ctx, body, angle)
    else:
        title, hook, mood, scenes = _generate_long(topic, lang, preset, llm, seconds, words, ctx, body)
    if angle is not None:
        title = angle.title
    return Script(title=title, hook=hook, lang=lang, format=fmt, mood=normalize_mood(mood),
                  scenes=postprocess(scenes, preset.max_ai_video),
                  sources=[SourceRef(title=s.title, url=s.url) for s in sources])


def normalize_mood(mood: str) -> str:
    mood = mood.strip().casefold()
    return mood if mood in MOODS else ""


def _draft_words(draft) -> int:
    return sum(len(s.narration.split()) for s in draft.scenes)


class NewScene(LLMScene):
    after: int  # number of the current scene it follows (1 = right after the opening hook)


class ExpandedScenes(BaseModel):
    new_scenes: list[NewScene] = []


WORDS_PER_NEW_SCENE = 15


def expand_scenes(scenes: list[LLMScene], target_words: int, ctx: dict, lang: str,
                  llm: LLMChain, pin_last: bool = True, max_after: int | None = None) -> list[LLMScene] | None:
    """Grow a draft toward target_words by INSERTING scenes built from unused facts.
    Small local models ignore "write longer" when rewriting from scratch (real drafts stayed at
    117-164 words for a ~200 target); extending a draft they can see works far better. The model only
    writes the new scenes and says where each goes; the originals are never sent back, because asked
    to return the full list it reordered them (seen live: a 77-word English short stayed 77 words).
    New scenes never go before the opening hook nor (pin_last) after the closing question.
    Returns None if nothing was added."""
    current = sum(len(s.narration.split()) for s in scenes)
    last = len(scenes) - 1 if pin_last else len(scenes)  # new scenes may follow scenes 1..last
    if max_after is not None:
        last = min(last, max_after)
    if current >= target_words or last < 1:  # a lone closing question has no room after it
        return None
    placement = ("Scene 1 is the opening hook and the last scene is the closing question: never put a new "
                 f"scene before 1 or after the last one (`after` from 1 to {last})." if pin_last else
                 f"These scenes are one part of a longer video: `after` from 1 to {last}; a new scene may "
                 "also follow the last one.")
    listing = "\n".join(f"{k}. {s.narration}" for k, s in enumerate(scenes, 1))
    new_count = max(1, round((target_words - current) / WORDS_PER_NEW_SCENE))
    try:
        added = llm.generate(templates.prompt("expand.md", scenes=listing, current_words=current,
                                     target_words=target_words, new_scenes=new_count, count=len(scenes),
                                     placement=placement,
                                     lang_name=LANG_NAMES[lang], **ctx), ExpandedScenes).new_scenes
    except Exception as e:  # LLM trouble just means "keep the draft"
        log.warning("expanding the script failed: %s", e)
        return None
    known = {s.narration.strip() for s in scenes}
    # seen live: gpt-oss wrote the additions of a Vietnamese script in English
    added = [a for a in added if a.narration.strip() and a.narration.strip() not in known
             and in_language(a.narration, lang)]
    if not added:
        return None
    slots: dict[int, list[LLMScene]] = {}
    for a in added:
        slots.setdefault(min(max(a.after, 1), last), []).append(LLMScene(**a.model_dump(exclude={"after"})))
    out: list[LLMScene] = []
    for k, sc in enumerate(scenes, 1):
        out += [sc, *slots.get(k, [])]
    return out


def _generate_with_length(llm: LLMChain, prompt: str, schema, target_words: int, ctx: dict, lang: str,
                          pin_last: bool = True):
    """Draft, then if it's under MIN_LENGTH_RATIO of the target, expand it (see expand_scenes).
    pin_last: the draft ends with the video's closing question (a whole short, the final chapter)."""
    draft = llm.generate(prompt, schema)
    if _draft_words(draft) >= target_words * MIN_LENGTH_RATIO:
        return draft
    # an open loop is answered near the end: new scenes go before the answer, never after it
    payoff = getattr(draft, "payoff_scene", 0)
    max_after = payoff - 1 if 2 <= payoff <= len(draft.scenes) else None
    expanded = expand_scenes(draft.scenes, target_words, ctx, lang, llm, pin_last=pin_last, max_after=max_after)
    if not expanded:
        return draft
    update: dict = {"scenes": expanded}
    if max_after is not None:  # the answer scene moved down by the scenes inserted before it
        answer = draft.scenes[payoff - 1].narration
        update["payoff_scene"] = next(i for i, s in enumerate(expanded, 1) if s.narration == answer)
    return draft.model_copy(update=update)


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
    prompt = templates.prompt(
        "rewrite_scene.md", title=script.title, lang_name=LANG_NAMES[script.lang],
        angle=angle_block(angle, opening=scene.id == script.scenes[0].id),
        facts=facts_block(sources), prev=prev or "(none — this is the opening)", current=scene.narration,
        next=nxt or "(none — this is the ending)", instruction=instr, max_words=limit,
        current_words=current_words)
    out = llm.generate(prompt, LLMScene)
    got = len(out.narration.split())
    if got > limit:
        try:
            again = f"\n\nYour previous answer had {got} words: rewrite it with at most {limit} words, " \
                    "this scene's idea only."
            retry = llm.generate(lambda tier: prompt(tier) + again, LLMScene)
            if len(retry.narration.split()) < got:
                out = retry
        except Exception as e:  # the first answer is still usable
            log.warning("shorter rewrite failed: %s", e)
    if not out.visual_query.isascii():  # seen live: a Vietnamese query, useless for stock search
        out = out.model_copy(update={"visual_query": scene.visual_query})
    return out.model_copy(update={"alt_queries": clean_alt_queries(out.visual_query, out.alt_queries)})


def clean_alt_queries(main: str, alts: list[str]) -> list[str]:
    """English only (stock search), different from the main query and each other, at most MAX_ALT_QUERIES."""
    seen = {main.strip().casefold()}
    out = []
    for q in alts:
        q = q.strip()
        if q and q.isascii() and q.casefold() not in seen:
            seen.add(q.casefold())
            out.append(q)
    return out[:MAX_ALT_QUERIES]


def extend_script(script: Script, preset: FormatPreset, llm: LLMChain, sources: list[Source] | None = None,
                  angle: Angle | None = None) -> Script | None:
    """"Kéo dài" button: bring an existing script toward the middle of the format's target length.
    Long videos are extended chapter by chapter (a 100-scene prompt would not fit the context);
    the deficit is shared in proportion to each chapter's length. None if nothing could be added."""
    ctx = {"facts": facts_block(sources or []), "angle": angle_block(angle, opening=False)}
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
            drafts = [LLMScene(narration=s.narration, visual_query=s.visual_query, alt_queries=s.alt_queries,
                               visual_type=s.visual_type,
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


def _generate_short(topic, lang, preset, llm, seconds, words, ctx, body, angle=None):
    prompt = templates.prompt(
        "short.md", topic=topic, **ctx, lang_name=LANG_NAMES[lang], target_words=words,
        target_seconds=seconds, scene_range="8-14", max_ai_video=preset.max_ai_video,
    )
    draft = _generate_with_length(llm, prompt, ShortDraft, words, body, lang)
    if 1 <= draft.payoff_scene < len(draft.scenes) - 1:
        log.info("open loop answered in scene %d of %d: moving the answer to the end",
                 draft.payoff_scene, len(draft.scenes))
    scenes = _open_loop_structure([Scene(id=0, **s.model_dump()) for s in draft.scenes], draft.open_loop,
                                  angle, draft.payoff_scene)
    hook = fix_hook(scenes[0].narration, topic, lang, ctx, llm)
    scenes[0] = scenes[0].model_copy(update={"narration": hook})
    return draft.title, hook, draft.mood, scenes


def _similar(a: str, b: str, share: float = 0.6) -> bool:
    """Most of the shorter sentence's words also appear in the other (a paraphrase of the same line)."""
    wa, wb = {bare(w) for w in a.split()} - {""}, {bare(w) for w in b.split()} - {""}
    return bool(wa and wb) and len(wa & wb) >= share * min(len(wa), len(wb))


def _open_loop_structure(scenes: list[Scene], open_loop: str, angle: Angle | None, payoff: int) -> list[Scene]:
    """Hook → open question → … → answer → closing question, enforced in code: in live runs the 8B model
    opened with a plain fact instead of the chosen hook, asked no question, and answered it in scene 6
    of 15. The model's scenes are kept; missing beats are inserted (filmed like their neighbour) and the
    answer is moved to just before the closing question. `payoff` is 1-based, 0 when unknown."""
    scenes = list(scenes)
    if 1 <= payoff < len(scenes) - 1:  # answered too early: the reveal belongs at the end
        answer = scenes.pop(payoff - 1)
        scenes.insert(len(scenes) - 1, answer)  # before the closing question (pop first: len changes)
    scenes = [sc for i, sc in enumerate(scenes) if i == len(scenes) - 1 or not hooks.is_comment_cta(sc.narration)]
    if angle is not None and hooks.hook_problem(angle.hook) is None:
        stock = {"narration": angle.hook, "visual_type": "stock", "ai_prompt": ""}
        if _similar(scenes[0].narration, angle.hook):
            scenes[0] = scenes[0].model_copy(update=stock)  # a paraphrase: say the chosen hook instead
        elif not any(_similar(s.narration, angle.hook, 0.9) for s in scenes[:2]):
            scenes.insert(0, scenes[0].model_copy(update=stock))
    question = open_loop.strip()
    if (question and len(scenes) > 1 and not ends_with(scenes[1].narration, ("?",))
            and bare(question) not in {bare(s.narration) for s in scenes}):
        scenes.insert(1, scenes[1].model_copy(update={"narration": question, "visual_type": "stock", "ai_prompt": ""}))
    return scenes


def fix_hook(hook: str, topic: str, lang: str, ctx: dict, llm: LLMChain) -> str:
    """A usable first sentence: a generic opener is cut off for free; a hook still unusable (too long,
    nothing left after the cut) gets ONE rewrite; if that is no better, the original stays."""
    problem = hooks.hook_problem(hook)
    if problem is None:
        return hook
    cut = hooks.strip_generic_opener(hook)
    if cut and hooks.hook_problem(cut) is None:
        return cut
    try:
        new = llm.generate(templates.prompt("hook_fix.md", topic=topic, lang_name=LANG_NAMES[lang], hook=hook,
                                  problem=problem, facts=ctx["facts"], max_words=hooks.HOOK_MAX_WORDS),
                           HookFix).hook.strip()
    except Exception as e:
        log.warning("hook rewrite failed: %s", e)
        return hook
    if new and hooks.hook_problem(new) is None:
        return new
    log.info("hook rewrite still %s, keeping the original", hooks.hook_problem(new) or "empty")
    return hook


def _generate_long(topic, lang, preset, llm, seconds, words, ctx, body):
    n_chapters = max(3, round(seconds / SECONDS_PER_CHAPTER))
    outline = llm.generate(
        templates.prompt("long_outline.md", topic=topic, **ctx, lang_name=LANG_NAMES[lang],
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
        try:  # one chapter the model can't write must not lose the outline and every other chapter
            draft = _generate_with_length(
                llm,
                templates.prompt("long_chapter.md", title=outline.title, **body, outline=outline_text, chapter_index=i,
                        chapter_count=len(outline.chapters), chapter_title=ch.title,
                        chapter_summary=ch.summary, position_note=note, lang_name=LANG_NAMES[lang],
                        # spread the AI-video budget: one slot per chapter for the first N chapters
                        target_words=per_chapter, max_ai_video=1 if i <= preset.max_ai_video else 0),
                ChapterDraft, per_chapter, body, lang, pin_last=i == len(outline.chapters),
            )
        except Exception as e:
            log.warning("chapter %d (%s) failed, skipping it: %s", i, ch.title, e)
            continue
        scenes += [Scene(id=0, chapter=ch.title, **s.model_dump()) for s in draft.scenes]
    if len(scenes) == 1:
        raise LLMError("no chapter could be written")
    return outline.title, outline.hook, outline.mood, scenes


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
    shorter than MIN_SCENE_WORDS into a neighbour (a 2-word scene flashes by in under a second).
    The fold makes max_words a soft limit: a scene may end up to MIN_SCENE_WORDS - 1 words over."""
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


def default_ai_prompt(query: str) -> str:
    return f"photorealistic cinematic shot of {query}, natural lighting"


TYPOGRAPHY = str.maketrans({"\u2010": "-", "\u2011": "-", "\u2012": "-", "\u00a0": " ", "\u202f": " "})


def in_language(text: str, lang: str) -> bool:
    """Vietnamese narration always carries diacritics; an all-ASCII sentence is another language."""
    return lang != "vi" or not text.isascii() or len(text.split()) < 4


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
        query, alts = scene.visual_query, clean_alt_queries(scene.visual_query, scene.alt_queries)
        if not query.isascii():  # stock search is English-only (seen live: a Vietnamese query)
            if alts:
                query, alts = alts[0], alts[1:]
            elif out:
                query = out[-1].visual_query
        ai_prompt = scene.ai_prompt
        if vtype != "stock" and not ai_prompt:
            ai_prompt = default_ai_prompt(query)
        # typographic hyphens/spaces from hosted models ("14\u20114\u20112017") trip TTS and number matching
        for i, part in enumerate(split_narration(scene.narration.translate(TYPOGRAPHY))):
            out.append(scene.model_copy(update={
                "id": len(out) + 1,
                "visual_query": query,
                "alt_queries": alts,
                "narration": part,
                # only the first split part keeps the AI treatment; the rest use stock
                "visual_type": vtype if i == 0 else "stock",
                "ai_prompt": ai_prompt if i == 0 else "",
            }))
    return out
