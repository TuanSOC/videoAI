"""✨ Rough ideas → specific, hooky video topics. A suggestion the user accepts or edits, never applied on its
own; no research yet, so the prompt forbids adding any figure, date or claim the user didn't write."""

from pydantic import BaseModel

from vidgen.config import LANG_NAMES
from vidgen.script.brief import FORMAT_NOTES, MAX_TOPICS, SINGLE_IDEA_MAX_CHARS, _clean_lines
from vidgen.script.llm import LLMChain
from vidgen.script.templates import prompt

MIN_CHARS = 8


class TopicIdea(BaseModel):
    topic: str
    reason: str


class TopicIdeas(BaseModel):
    ideas: list[TopicIdea]


def enhance_topics(text: str, lang: str, fmt: str, llm: LLMChain) -> list[TopicIdea]:
    """One call for all pasted ideas (at most MAX_TOPICS). Raises ValueError when nothing usable came back."""
    lines = _clean_lines(text)[:MAX_TOPICS] or [text.strip()]
    result = llm.generate(prompt("enhance_topic.md", ideas="\n".join(f"- {ln}" for ln in lines),
                                 count=len(lines), lang_name=LANG_NAMES[lang], format_note=FORMAT_NOTES[fmt]),
                          TopicIdeas)
    # a topic over SINGLE_IDEA_MAX_CHARS would be re-split as notes when the video is created
    ideas = [TopicIdea(topic=i.topic.strip(), reason=i.reason.strip()) for i in result.ideas
             if MIN_CHARS <= len(i.topic.strip()) <= SINGLE_IDEA_MAX_CHARS][:len(lines)]
    if not ideas:
        raise ValueError("the model returned no usable topic")
    return ideas
