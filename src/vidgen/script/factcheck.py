"""Check a script's scenes against its sources; flags live in factcheck.json, keyed by narration text.

Kept outside script.json on purpose: writing flags into the script would change its hash and force
the voice to be regenerated, and keying by text means editing a flagged sentence clears its flag."""

import logging
from pathlib import Path
from string import Template

from pydantic import BaseModel

from vidgen.models import Script
from vidgen.script.llm import LLMChain
from vidgen.script.research import Source, facts_block

log = logging.getLogger(__name__)
PROMPT = Path(__file__).parent / "prompts" / "factcheck.md"
FILE = "factcheck.json"
SCENES_PER_CHECK = 25


class Issue(BaseModel):
    id: int
    note: str


class Issues(BaseModel):
    issues: list[Issue] = []


class FactCheck(BaseModel):
    """What was checked and what looks wrong. `issues[].narration` is the exact sentence flagged."""
    checked: bool          # False: no sources to check against
    issues: list[dict] = []  # {"scene_id", "narration", "note"}


def fact_check(script: Script, sources: list[Source], llm: LLMChain) -> FactCheck:
    from vidgen.script.writer import LANG_NAMES

    if not sources:
        return FactCheck(checked=False)
    template = Template(PROMPT.read_text(encoding="utf-8"))
    found: list[Issue] = []
    # batches: a 100-scene long video in one prompt would overflow the 8k context
    for i in range(0, len(script.scenes), SCENES_PER_CHECK):
        batch = script.scenes[i:i + SCENES_PER_CHECK]
        prompt = template.substitute(facts=facts_block(sources), lang_name=LANG_NAMES[script.lang],
                                     scenes="\n".join(f"{s.id}. {s.narration}" for s in batch))
        found += llm.generate(prompt, Issues).issues
    by_id = {s.id: s for s in script.scenes}
    # the model sometimes "reports" questions just to say they need no check (seen live): drop them
    return FactCheck(checked=True, issues=[
        {"scene_id": i.id, "narration": by_id[i.id].narration, "note": i.note.strip()}
        for i in found
        if i.id in by_id and i.note.strip() and not by_id[i.id].narration.rstrip().endswith("?")])
