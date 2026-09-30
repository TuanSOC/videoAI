"""Prompt files in script/prompts/, filled with string.Template. One renderer for every module; the
mood list comes from code (assemble/music.MOODS), not a copy in the prompt text.

prompts/strong/<name> is a richer variant for large hosted models (Groq); the local 8B keeps the base file.
The chain picks per provider (llm.LLMChain.generate), so a fallback to Ollama gets the base prompt."""

from pathlib import Path
from string import Template
from typing import Callable

from vidgen.assemble.looks import LOOKS
from vidgen.assemble.music import MOODS

PROMPTS = Path(__file__).parent / "prompts"


def render(name: str, tier: str = "base", **values) -> str:
    """A missing $placeholder raises KeyError: better at the first call than a half-filled prompt."""
    values.setdefault("moods", ", ".join(MOODS))
    values.setdefault("looks", ", ".join(LOOKS))
    path = PROMPTS / "strong" / name if tier == "strong" and (PROMPTS / "strong" / name).exists() else PROMPTS / name
    return Template(path.read_text(encoding="utf-8")).substitute(**values)


def prompt(name: str, **values) -> Callable[[str], str]:
    """The prompt for whichever tier ends up answering (for LLMChain.generate)."""
    return lambda tier: render(name, tier=tier, **values)
