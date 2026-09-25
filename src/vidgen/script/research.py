"""Wikipedia grounding: fetch the most relevant passages about a topic so a small local LLM writes from
facts instead of inventing them. Free API, no key. Failures degrade to "no sources", never block."""

import logging
import re
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field

from vidgen.script.llm import LLMChain

log = logging.getLogger(__name__)
USER_AGENT = "vidgen/0.1 (local faceless-video tool)"  # Wikipedia API etiquette requires one
CHARS_PER_SOURCE = 3000     # two sources ≈ 6k chars: fits the 8k-token context with the script prompt
MIN_PARAGRAPH_CHARS = 80    # skips headings, captions, list stubs
STOP_HEADINGS = {           # everything after these is citations/links, not facts
    "references", "external links", "see also", "notes", "further reading", "bibliography",
    "tham khảo", "liên kết ngoài", "xem thêm", "chú thích", "đọc thêm", "ghi chú",
}


class ResearchPlan(BaseModel):
    query_en: str = Field(description="English Wikipedia search query: the main subject, 1-3 words")
    query_local: str = Field(description="Same subject as a search query in the video language")
    keywords: list[str] = Field(description="6-12 single words, English AND video language, "
                                            "naming the specific aspect the video is about")


@dataclass
class Source:
    title: str
    url: str
    lang: str
    text: str  # selected passages


PLAN_PROMPT = """A short video will be made about: {topic}
Video language: {lang_name}.
Plan a Wikipedia lookup to fact-check it.
- `query_en`: the main subject as an English Wikipedia search (e.g. "Octopus", "Bermuda Triangle").
- `query_local`: the same subject in {lang_name}.
- `keywords`: 6-12 single words in BOTH English and {lang_name} for the specific aspect asked about
  (e.g. for "why octopuses have three hearts": heart, hearts, blood, circulatory, gill, tim, máu, mang).
"""


def _words(text: str) -> set[str]:
    return set(re.findall(r"\w+", text.casefold()))


def select_passages(extract: str, keywords: list[str], budget: int = CHARS_PER_SOURCE) -> str:
    """Intro paragraph + paragraphs with the most whole-word keyword hits, in article order."""
    paragraphs: list[str] = []
    for line in (ln.strip() for ln in extract.split("\n")):
        if line.casefold().rstrip(":") in STOP_HEADINGS:
            break
        if len(line) >= MIN_PARAGRAPH_CHARS:
            paragraphs.append(line)
    if not paragraphs:
        return ""
    kw = {k.casefold() for k in keywords if k.strip()}
    scored = sorted(((len(_words(p) & kw), i) for i, p in enumerate(paragraphs[1:], 1)), reverse=True)
    chosen, used = {0}, len(paragraphs[0])
    for score, i in scored:
        if score == 0:
            break
        if used + len(paragraphs[i]) > budget:
            continue
        chosen.add(i)
        used += len(paragraphs[i])
    text = "\n\n".join(paragraphs[i] for i in sorted(chosen))
    return text[:budget]


class Wikipedia:
    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT})

    def _api(self, lang: str, **params) -> dict:
        resp = self.http.get(f"https://{lang}.wikipedia.org/w/api.php", params={"format": "json", **params})
        resp.raise_for_status()
        return resp.json()

    def lookup(self, lang: str, query: str, keywords: list[str]) -> Source | None:
        hits = self._api(lang, action="query", list="search", srsearch=query, srlimit=1)["query"]["search"]
        if not hits:
            return None
        pages = self._api(lang, action="query", prop="extracts|info", inprop="url", explaintext=1,
                          exsectionformat="plain", redirects=1, titles=hits[0]["title"])["query"]["pages"]
        page = next(iter(pages.values()))
        text = select_passages(page.get("extract", ""), keywords)
        return Source(page["title"], page.get("fullurl", ""), lang, text) if text else None


def research(topic: str, lang: str, llm: LLMChain, wiki: Wikipedia | None = None) -> list[Source]:
    """English Wikipedia (usually the most detailed) plus the video-language edition."""
    from vidgen.script.writer import LANG_NAMES

    try:
        plan = llm.generate(PLAN_PROMPT.format(topic=topic, lang_name=LANG_NAMES[lang]), ResearchPlan)
    except Exception as e:
        log.warning("research plan failed, writing without sources: %s", e)
        return []
    wiki = wiki or Wikipedia()
    lookups = [("en", plan.query_en)] + ([(lang, plan.query_local)] if lang != "en" else [])
    sources = []
    for wlang, query in lookups:
        try:
            src = wiki.lookup(wlang, query, plan.keywords)
        except (httpx.HTTPError, KeyError, ValueError) as e:
            log.warning("wikipedia %s lookup %r failed: %s", wlang, query, e)
            continue
        if src:
            sources.append(src)
    return sources


def facts_block(sources: list[Source]) -> str:
    if not sources:
        return ("No reference sources were found. Stay general: no specific numbers, dates, "
                "names or surprising claims you are not certain of.")
    parts = [f"[{i}] {s.title} ({s.lang}.wikipedia.org)\n{s.text}" for i, s in enumerate(sources, 1)]
    return ("Use ONLY facts stated in these sources. If a detail is not in them, leave it out — "
            "do not guess or embellish. Sources may be in another language; write in the video language.\n\n"
            + "\n\n".join(parts))
