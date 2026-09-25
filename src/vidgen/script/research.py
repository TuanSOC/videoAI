"""Wikipedia grounding: fetch the most relevant passages about a topic so a small local LLM writes from
facts instead of inventing them. Free API, no key. Failures degrade to "no sources", never block."""

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from vidgen.fsutil import write_atomic
from vidgen.models import SourceDoc
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
    queries_en: list[str] = Field(description="1-3 likely English Wikipedia article titles (noun phrases)")
    queries_local: list[str] = Field(description="1-2 likely article titles in the video language")
    keywords: list[str] = Field(description="6-12 single words, English AND video language, "
                                            "naming the specific aspect the video is about")


Source = SourceDoc  # title, url, lang, text (selected passages)


PLAN_PROMPT = """A short video will be made about: {topic}
Video language: {lang_name}.
Plan a Wikipedia lookup to fact-check it.
- `queries_en`: 1-3 English Wikipedia ARTICLE TITLES that would contain the facts — short noun phrases,
  NEVER questions (e.g. for "why is the sea salty": "Seawater", "Salinity"; for octopus hearts: "Octopus").
- `queries_local`: 1-2 article titles in {lang_name} (e.g. "Nước biển", "Bạch tuộc").
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


SEARCH_LIMIT = 5
RATE_LIMIT_RETRIES = 3
MAX_RETRY_WAIT = 20.0  # seconds; Wikipedia usually asks for ~10


@dataclass(frozen=True)
class Hit:
    title: str
    snippet: str


class ArticlePick(BaseModel):
    index: int = Field(description="0-based index of the right article, or -1 if none fits")
    reason: str = ""


PICK_PROMPT = """A fact-based short video explains: {topic}
Which Wikipedia article is the encyclopedic source about the real-world subject of this video?

Candidates:
{candidates}

Rules: pick the article whose scope matches the topic. A general topic gets the general article
("octopuses" → "Octopus", not one species like "Giant Pacific octopus"); a specific question gets the
article that answers it ("why the sea is salty" → "Seawater" rather than "Ocean"; octopus hearts →
"Octopus" rather than "Cephalopod"). Reject fairy tales, legends, novels,
films, songs, albums, people, companies, other species with similar names, and disambiguation pages.
If none fits, answer index -1."""

_TAGS = re.compile(r"<[^>]+>")


def pick_article(topic: str, hits: list[Hit], llm: LLMChain) -> Hit | None:
    """Let the LLM judge relevance: the top search hit is often a namesake (fairy tale, band, animal)."""
    if not hits:
        return None
    listing = "\n".join(f"{i}. {h.title} — {h.snippet}" for i, h in enumerate(hits))
    try:
        pick = llm.generate(PICK_PROMPT.format(topic=topic, candidates=listing), ArticlePick)
    except Exception as e:
        log.warning("article pick failed (%s); skipping this source", e)
        return None
    return hits[pick.index] if 0 <= pick.index < len(hits) else None


class Wikipedia:
    """Wikipedia API client. Responses are cached on disk (same topic/angles never hit the API twice)
    and 429 rate limits are waited out per Retry-After, as Wikimedia's API etiquette asks."""

    def __init__(self, http: httpx.Client | None = None, cache_dir: Path | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.http = http or httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT})
        self.cache_dir = cache_dir
        self.sleep = sleep

    def _api(self, lang: str, **params) -> dict:
        params = {"format": "json", **params}
        cached = None
        if self.cache_dir is not None:
            key = hashlib.sha1(json.dumps([lang, params], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            cached = self.cache_dir / f"{key}.json"
            if cached.exists():
                return json.loads(cached.read_text(encoding="utf-8"))
        for attempt in range(RATE_LIMIT_RETRIES + 1):
            resp = self.http.get(f"https://{lang}.wikipedia.org/w/api.php", params=params)
            if resp.status_code != 429 or attempt == RATE_LIMIT_RETRIES:
                break
            wait = min(float(resp.headers.get("Retry-After", "5") or 5), MAX_RETRY_WAIT)
            log.warning("wikipedia rate limit, waiting %.0fs", wait)
            self.sleep(wait)
        resp.raise_for_status()
        data = resp.json()
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True)
            write_atomic(cached, json.dumps(data, ensure_ascii=False))
        return data

    def search(self, lang: str, query: str, limit: int = SEARCH_LIMIT) -> list[Hit]:
        hits = self._api(lang, action="query", list="search", srsearch=query, srlimit=limit)["query"]["search"]
        return [Hit(h["title"], _TAGS.sub("", h.get("snippet", ""))) for h in hits]

    def fetch(self, lang: str, title: str, keywords: list[str]) -> Source | None:
        pages = self._api(lang, action="query", prop="extracts|info", inprop="url", explaintext=1,
                          exsectionformat="plain", redirects=1, titles=title)["query"]["pages"]
        page = next(iter(pages.values()))
        text = select_passages(page.get("extract", ""), keywords)
        return Source(title=page["title"], url=page.get("fullurl", ""), lang=lang, text=text) if text else None


MAX_CANDIDATES = 8


def default_wikipedia() -> Wikipedia:
    from vidgen.config import get_settings

    s = get_settings()
    return Wikipedia(cache_dir=s.path(s.pipeline.cache_dir) / "wiki")


def lookup_sources(topic: str, lang: str, queries_en: list[str], queries_local: list[str],
                   keywords: list[str], llm: LLMChain, wiki: Wikipedia | None = None,
                   cache: dict | None = None) -> list[Source]:
    """One article from English Wikipedia (usually the most detailed) plus one from the video-language
    edition. Each language pools the hits of all its queries, then the LLM picks one.
    `cache` (keyed by lang + queries) lets several brief angles share lookups within one job."""
    wiki = wiki or default_wikipedia()
    cache = {} if cache is None else cache
    lookups = [("en", queries_en)] + ([(lang, queries_local)] if lang != "en" else [])
    sources: list[Source] = []
    for wlang, queries in lookups:
        queries = [q.strip() for q in queries if q and q.strip()]
        if not queries:
            continue
        key = (wlang, tuple(sorted(q.casefold() for q in queries)))
        if key not in cache:
            try:
                pooled: dict[str, Hit] = {}
                for q in queries:
                    for h in wiki.search(wlang, q):
                        pooled.setdefault(h.title, h)
                hit = pick_article(topic, list(pooled.values())[:MAX_CANDIDATES], llm)
                cache[key] = wiki.fetch(wlang, hit.title, keywords) if hit else None
            except (httpx.HTTPError, KeyError, ValueError) as e:
                log.warning("wikipedia %s lookup %r failed: %s", wlang, queries, e)
                cache[key] = None
        src = cache[key]
        if src and src.url not in {s.url for s in sources}:
            sources.append(src)
    return sources


def research(topic: str, lang: str, llm: LLMChain, wiki: Wikipedia | None = None) -> list[Source]:
    """Plan queries with the LLM, then look them up (used when a video has no brief)."""
    from vidgen.script.writer import LANG_NAMES

    try:
        plan = llm.generate(PLAN_PROMPT.format(topic=topic, lang_name=LANG_NAMES[lang]), ResearchPlan)
    except Exception as e:
        log.warning("research plan failed, writing without sources: %s", e)
        return []
    return lookup_sources(topic, lang, plan.queries_en, plan.queries_local, plan.keywords, llm, wiki)


def facts_block(sources: list[Source]) -> str:
    if not sources:
        return ("No reference sources were found. Stay general: no specific numbers, dates, "
                "names or surprising claims you are not certain of.")
    parts = [f"[{i}] {s.title} ({s.lang}.wikipedia.org)\n{s.text}" for i, s in enumerate(sources, 1)]
    return ("Use ONLY facts stated in these sources. If a detail is not in them, leave it out — "
            "do not guess or embellish. Sources may be in another language; write in the video language.\n\n"
            + "\n\n".join(parts))
