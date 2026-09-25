"""Brief step: rough ideas → clean topics → 3 angles per topic, each with its own Wikipedia sources.

The user picks (and may edit) one angle before any script is written, so a vague or noisy input
("bạch tuộc", a pasted list with notes) never goes straight into the script prompt."""

import logging
import re
from pathlib import Path
from string import Template

from pydantic import BaseModel, Field

from vidgen.models import Angle, AngleStyle, Brief
from vidgen.script.llm import LLMChain
from vidgen.script.research import Wikipedia, facts_block
from vidgen.script.research import research as research_topic

log = logging.getLogger(__name__)
PROMPTS = Path(__file__).parent / "prompts"
MAX_TOPICS = 5
SINGLE_IDEA_MAX_CHARS = 120
_BULLET = re.compile(r"^\s*(?:[-*•]+|\d+[.)])\s*")
FORMAT_NOTES = {"short": "vertical short, 45-75 seconds", "long": "horizontal YouTube video, 8-15 minutes"}


class TopicList(BaseModel):
    topics: list[str] = Field(min_length=1)


class AngleDraft(BaseModel):
    style: AngleStyle
    title: str
    hook: str
    key_points: list[str] = Field(min_length=2)
    keywords: list[str] = []


class AngleList(BaseModel):
    angles: list[AngleDraft] = Field(min_length=1)


def _render(name: str, **values) -> str:
    return Template((PROMPTS / name).read_text(encoding="utf-8")).substitute(**values)


def _clean_lines(text: str) -> list[str]:
    return [_BULLET.sub("", ln).strip() for ln in text.splitlines() if _BULLET.sub("", ln).strip()]


def is_single_idea(text: str) -> bool:
    t = text.strip()
    return "\n" not in t and len(t) <= SINGLE_IDEA_MAX_CHARS


def split_ideas(text: str, lang: str, llm: LLMChain) -> list[str]:
    """One short line → itself (no LLM call). Otherwise the LLM separates topics and drops notes;
    if it fails, fall back to one topic per non-empty line."""
    from vidgen.script.writer import LANG_NAMES

    if is_single_idea(text):
        return [text.strip()]
    try:
        result = llm.generate(_render("split_ideas.md", text=text.strip(), max_topics=MAX_TOPICS,
                                      lang_name=LANG_NAMES[lang]), TopicList)
        topics = [t.strip() for t in result.topics if t.strip()]
    except Exception as e:
        log.warning("idea split failed (%s); using one topic per line", e)
        topics = _clean_lines(text)
    return list(dict.fromkeys(topics))[:MAX_TOPICS] or [text.strip()[:SINGLE_IDEA_MAX_CHARS]]


def make_brief(topic: str, lang: str, fmt: str, llm: LLMChain, wiki: Wikipedia | None = None,
               research: bool = True) -> Brief:
    """Research the topic ONCE, then write the 3 angles (explain / myth / story) from those facts.

    Order matters: angles written before research invented their key points (a "2005 Japanese
    scientist" story), and per-angle lookups cost 6 article picks and disagreed on the article."""
    from vidgen.script.writer import LANG_NAMES

    sources = research_topic(topic, lang, llm, wiki) if research else []
    drafts = llm.generate(_render("brief_angles.md", topic=topic, lang_name=LANG_NAMES[lang],
                                  format_note=FORMAT_NOTES[fmt], facts=facts_block(sources)), AngleList).angles
    # keep one angle per style, in the canonical order, even if the model repeated a style
    by_style: dict[str, AngleDraft] = {}
    for d in drafts:
        by_style.setdefault(d.style, d)
    ordered = [by_style[s] for s in ("explain", "myth", "story") if s in by_style]
    return Brief(topic=topic, angles=[Angle(**d.model_dump(), sources=sources) for d in ordered])
