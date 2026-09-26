"""Prompt files in script/prompts/, filled with string.Template. One renderer for every module; the
mood list comes from code (assemble/music.MOODS), not a copy in the prompt text."""

from pathlib import Path
from string import Template

from vidgen.assemble.music import MOODS

PROMPTS = Path(__file__).parent / "prompts"


def render(name: str, **values) -> str:
    """A missing $placeholder raises KeyError: better at the first call than a half-filled prompt."""
    values.setdefault("moods", ", ".join(MOODS))
    return Template((PROMPTS / name).read_text(encoding="utf-8")).substitute(**values)
