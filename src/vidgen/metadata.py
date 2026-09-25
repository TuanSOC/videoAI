"""Upload metadata: LLM-written title/description/tags + asset credits + AI disclosure."""

from pathlib import Path

from pydantic import BaseModel, Field

from vidgen.fsutil import write_atomic
from vidgen.models import Asset, Script
from vidgen.script.llm import LLMChain
from vidgen.script.writer import LANG_NAMES

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


def generate_metadata(script: Script, assets: list[Asset], llm: LLMChain) -> Metadata:
    narration = " ".join(s.narration for s in script.scenes)[:NARRATION_CHARS]
    llm_meta = llm.generate(PROMPT.format(
        kind="vertical short (TikTok/Shorts/Reels)" if script.format == "short" else "YouTube long-form",
        lang_name=LANG_NAMES[script.lang], title=script.title, narration=narration,
        shorts_rule="; include #shorts" if script.format == "short" else "",
    ), LLMMetadata)

    credits = credit_lines(assets)
    hashtags = normalize_hashtags(llm_meta.hashtags, script.format == "short")
    disclosure = DISCLOSURE[script.lang]
    description = llm_meta.description.strip() + f"\n\n{disclosure}"
    if credits:
        description += "\n\nFootage:\n" + "\n".join(f"- {c}" for c in credits)
    description += "\n\n" + " ".join(hashtags)

    return Metadata(
        title=llm_meta.title.strip()[:TITLE_MAX], description=description, tags=llm_meta.tags,
        hashtags=hashtags, credits=credits, ai_disclosure=disclosure,
        ai_visuals_used=any(a.source in ("flux", "wan") for a in assets),
    )


def save_metadata(meta: Metadata, out: Path) -> None:
    write_atomic(out, meta.model_dump_json(indent=2))
