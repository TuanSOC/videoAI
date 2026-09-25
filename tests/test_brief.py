import json

import httpx
import pytest

from vidgen import pipeline
from vidgen.config import get_settings
from vidgen.models import Angle, Brief, SourceDoc
from vidgen.script import brief as br
from vidgen.script import research as rs
from vidgen.script import writer
from vidgen.script.llm import LLMChain


class SchemaLLM:
    """Answers by response schema name; article picks default to the first candidate."""
    name = "fake"

    def __init__(self, **by_schema):
        self.by_schema = by_schema
        self.prompts: list[tuple[str, str]] = []

    def generate_json(self, prompt, schema):
        self.prompts.append((schema.__name__, prompt))
        if schema.__name__ == "ArticlePick":
            return json.dumps({"index": 0})
        r = self.by_schema[schema.__name__]
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r)


def angle_draft(style, q="Octopus"):
    return {"style": style, "title": f"{style} title", "hook": f"{style} hook",
            "key_points": ["p1", "p2", "p3"], "queries_en": [q], "queries_local": ["Bạch tuộc"],
            "keywords": ["heart"]}


def fake_wiki(calls=None):
    article = "\n".join([  # paragraphs must be ≥ MIN_PARAGRAPH_CHARS to be kept
        "The octopus is a soft-bodied, eight-limbed mollusc of the order Octopoda, found in every ocean.",
        "Octopuses have three hearts; two branchial hearts pump blood through the gills and one the body."])

    def handler(req):
        if calls is not None:
            calls.append(req.url.params.get("list") or "extract")
        p = req.url.params
        if p.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [{"title": p["srsearch"], "snippet": ""}]}})
        return httpx.Response(200, json={"query": {"pages": {"1": {
            "title": p["titles"], "fullurl": f"https://x/{p['titles']}", "extract": article}}}})
    return rs.Wikipedia(httpx.Client(transport=httpx.MockTransport(handler)))


# --- split ------------------------------------------------------------------------------------
def test_single_line_idea_skips_llm():
    llm = SchemaLLM()
    assert br.split_ideas("  bạch tuộc  ", "vi", LLMChain([llm])) == ["bạch tuộc"]
    assert llm.prompts == []


def test_multi_topic_notes_split_by_llm():
    notes = ("Thiên nhiên / địa lý:\n- Vì sao nước biển mặn: có cảnh sóng, sông.\n"
             "- Bí ẩn cực quang phương Bắc: cảnh rất đẹp.")
    llm = SchemaLLM(TopicList={"topics": ["Vì sao nước biển mặn", "Bí ẩn cực quang phương Bắc",
                                          "Vì sao nước biển mặn"]})
    assert br.split_ideas(notes, "vi", LLMChain([llm])) == ["Vì sao nước biển mặn", "Bí ẩn cực quang phương Bắc"]
    assert "Thiên nhiên / địa lý" in llm.prompts[0][1]


def test_split_falls_back_to_lines_and_caps():
    llm = SchemaLLM(TopicList=RuntimeError("down"))
    text = "\n".join(f"- ý tưởng {i}" for i in range(8))
    assert br.split_ideas(text, "vi", LLMChain([llm])) == [f"ý tưởng {i}" for i in range(5)]


# --- brief ------------------------------------------------------------------------------------
PLAN = {"queries_en": ["Octopus"], "queries_local": ["Bạch tuộc"], "keywords": ["hearts"]}


def test_make_brief_researches_once_then_writes_angles_from_facts():
    calls = []
    llm = SchemaLLM(ResearchPlan=PLAN,
                    AngleList={"angles": [angle_draft("story"), angle_draft("explain"),
                                          angle_draft("myth"), angle_draft("explain", q="Other")]})
    brief = br.make_brief("bạch tuộc", "vi", "short", LLMChain([llm]), fake_wiki(calls))
    assert [a.style for a in brief.angles] == ["explain", "myth", "story"]  # canonical order, deduped
    assert all([s.title for s in a.sources] == ["Octopus", "Bạch tuộc"] for a in brief.angles)
    assert calls.count("search") == 2  # one lookup per language for the whole topic
    order = [name for name, _ in llm.prompts if name != "ArticlePick"]
    assert order == ["ResearchPlan", "AngleList"]  # research BEFORE angles
    angles_prompt = [p for name, p in llm.prompts if name == "AngleList"][0]
    assert "three hearts" in angles_prompt and "Never invent people" in angles_prompt


def test_make_brief_without_research():
    llm = SchemaLLM(AngleList={"angles": [angle_draft("explain")]})
    brief = br.make_brief("x", "en", "long", LLMChain([llm]), research=False)
    assert brief.angles[0].sources == [] and "8-15 minutes" in llm.prompts[0][1]


def test_brief_chosen_sources_respects_exclusions():
    a = SourceDoc(title="A", url="u1", lang="en", text="t")
    b = SourceDoc(title="B", url="u2", lang="vi", text="t")
    brief = Brief(topic="t", angles=[Angle(style="explain", title="T", hook="H", key_points=["k"], sources=[a, b])],
                  chosen=0, excluded_urls=["u2"])
    assert [s.title for s in brief.chosen_sources()] == ["A"]
    assert Brief(topic="t", angles=brief.angles).chosen_sources() == []


# --- writer follows the angle -----------------------------------------------------------------
def test_writer_uses_angle_title_hook_and_points():
    angle = Angle(style="myth", title="Tiêu đề của tôi", hook="Bạn tưởng biển mặn vì muối mưa?",
                  key_points=["Muối từ đá", "Sông mang khoáng chất"])
    scene = {"narration": " ".join(["từ"] * 20) + ".", "visual_query": "sea", "visual_type": "stock", "ai_prompt": ""}
    llm = SchemaLLM(ShortDraft={"title": "LLM title", "hook": "h", "scenes": [scene] * 12})
    script = writer.generate("x", "short", "vi", get_settings().preset("short"), LLMChain([llm]), angle=angle)
    prompt = llm.prompts[0][1]
    assert "Bạn tưởng biển mặn vì muối mưa?" in prompt and "- Sông mang khoáng chất" in prompt
    assert script.title == "Tiêu đề của tôi"


# --- pipeline choose_angle --------------------------------------------------------------------
def write_brief_file(d):
    d.mkdir(parents=True, exist_ok=True)
    brief = Brief(topic="t", angles=[Angle(style=s, title=f"{s}", hook="h", key_points=["a"]) for s in
                                     ("explain", "myth", "story")])
    (d / pipeline.BRIEF_FILE).write_text(brief.model_dump_json(), encoding="utf-8")


def test_choose_angle_applies_edits(tmp_path):
    write_brief_file(tmp_path)
    brief = pipeline.choose_angle(tmp_path, 1, title="  Mới  ", hook="", key_points=["x", " ", "y"],
                                  excluded_urls=["u"])
    assert brief.chosen == 1 and brief.angles[1].title == "Mới"
    assert brief.angles[1].hook == "h"  # empty edit keeps the original
    assert brief.angles[1].key_points == ["x", "y"] and brief.excluded_urls == ["u"]
    assert pipeline.load_brief(tmp_path).chosen == 1


def test_choose_angle_errors(tmp_path):
    with pytest.raises(ValueError):
        pipeline.choose_angle(tmp_path, 0)
    write_brief_file(tmp_path)
    with pytest.raises(IndexError):
        pipeline.choose_angle(tmp_path, 3)
