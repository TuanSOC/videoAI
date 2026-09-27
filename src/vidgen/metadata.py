"""Upload metadata: LLM-written title/description/tags + asset credits + AI disclosure."""

from pathlib import Path

from pydantic import BaseModel, Field

from vidgen.fsutil import write_atomic
from vidgen.text import plain
from vidgen.models import Asset, Script
from vidgen.script.llm import LLMChain
from vidgen.config import LANG_NAMES

DISCLOSURE = {
    "vi": "Video sử dụng giọng đọc AI; một số hình ảnh minh họa được tạo bằng AI.",
    "en": "This video uses an AI voice; some illustrative visuals are AI-generated.",
}
NARRATION_CHARS = 3000


TITLE_MAX = 100  # YouTube hard limit; the prompt asks for <70


class LLMMetadata(BaseModel):
    title: str
    description: str
    tags: list[str]
    hashtags: list[str]


class Metadata(LLMMetadata):
    credits: list[str]
    ai_disclosure: str
    ai_visuals_used: bool = Field(description="True → tick the platform's 'altered/synthetic content' label")
    summary: str = ""  # the LLM's own text, so `description` can be rebuilt after a clip swap without the LLM


PROMPT = """Write upload metadata for a {kind} video in {lang_name}.

Working title: {title}
Narration:
{narration}

Rules:
- `title`: under 70 characters, curiosity-driven, no clickbait lies, in {lang_name}.
- `description`: 2 short paragraphs in {lang_name} summarising what viewers learn; no hashtags, no links.
- `tags`: 10-15 search keywords (mix of {lang_name} and English).
- `hashtags`: 3-5 hashtags without spaces, each starting with #{shorts_rule}
"""


def normalize_hashtags(tags: list[str], short: bool) -> list[str]:
    out: list[str] = []
    for t in tags:
        tag = "#" + "".join(t.split()).lstrip("#")
        if len(tag) > 1 and tag.casefold() not in (x.casefold() for x in out):
            out.append(tag)
    if short and "#shorts" not in (x.casefold() for x in out):
        out.append("#shorts")
    return out


def credit_lines(assets: list[Asset]) -> list[str]:
    seen, lines = set(), []
    for a in assets:
        if a.source in ("pexels", "pixabay") and a.url not in seen:
            seen.add(a.url)
            by = f" by {a.author}" if a.author else ""
            lines.append(f"{a.source.capitalize()}{by}: {a.url}")
    return lines


def generate_metadata(script: Script, assets: list[Asset], llm: LLMChain, music_credit: str = "") -> Metadata:
    narration = " ".join(s.narration for s in script.scenes)[:NARRATION_CHARS]
    llm_meta = llm.generate(PROMPT.format(
        kind="vertical short (TikTok/Shorts/Reels)" if script.format == "short" else "YouTube long-form",
        lang_name=LANG_NAMES[script.lang], title=script.title, narration=narration,
        shorts_rule="; include #shorts" if script.format == "short" else "",
    ), LLMMetadata)

    return _compose(plain(llm_meta.title.strip())[:TITLE_MAX], plain(llm_meta.description).strip(), llm_meta.tags,
                    normalize_hashtags(llm_meta.hashtags, script.format == "short"), script, assets, music_credit)


def _compose(title: str, summary: str, tags: list[str], hashtags: list[str], script: Script,
             assets: list[Asset], music_credit: str = "") -> Metadata:
    """Deterministic part of the description: disclosure, sources, footage credits, hashtags."""
    credits = credit_lines(assets)
    disclosure = DISCLOSURE[script.lang]
    description = summary + f"\n\n{disclosure}"
    if script.sources:
        label = "Nguồn tham khảo" if script.lang == "vi" else "Sources"
        description += f"\n\n{label}:\n" + "\n".join(f"- {s.title}: {s.url}" for s in script.sources)
    if credits:
        description += "\n\nFootage:\n" + "\n".join(f"- {c}" for c in credits)
    if music_credit:  # CC BY tracks: the license requires this line
        description += f"\n\nMusic:\n- {music_credit}"
    description += "\n\n" + " ".join(hashtags)
    return Metadata(title=title, description=description, tags=tags, hashtags=hashtags, credits=credits,
                    ai_disclosure=disclosure, summary=summary,
                    ai_visuals_used=any(a.source in ("flux", "wan") for a in assets))


def rebuild_description(meta: Metadata, script: Script, assets: list[Asset], music_credit: str = "") -> Metadata:
    """Refresh credits/AI flag after clips changed — no LLM call. Older files lack `summary`:
    it is the text before the disclosure line."""
    summary = meta.summary or meta.description.split(f"\n\n{meta.ai_disclosure}")[0].strip()
    return _compose(meta.title, summary, meta.tags, meta.hashtags, script, assets, music_credit)


def save_metadata(meta: Metadata, out: Path) -> None:
    write_atomic(out, meta.model_dump_json(indent=2))
