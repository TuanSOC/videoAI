import json

import httpx

from vidgen.config import get_settings
from vidgen.script import research as rs
from vidgen.script import writer
from vidgen.script.llm import LLMChain

INTRO = "The octopus is a soft-bodied, eight-limbed mollusc of the order Octopoda, found in every ocean."
HEART = ("Octopuses have three hearts; a systemic heart circulates blood around the body and two "
         "branchial hearts pump it through the gills. The systemic heart stops while swimming.")
DIET = "Octopuses are predators that hunt crabs, clams and fish, using their arms to grab prey quickly."
QUICKTIME = "Footage of an octopus eating a shark, also available in Quicktime format for older players."
ARTICLE = "\n".join([INTRO, "Anatomy", HEART, DIET, "References", "A cited paper about hearts " * 5])


class FakeLLM:
    name = "fake"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return r if isinstance(r, str) else json.dumps(r)


PLAN = {"query_en": "Octopus", "query_local": "Bạch tuộc", "keywords": ["heart", "hearts", "tim"]}


def test_select_keeps_intro_and_relevant_paragraphs_only():
    text = rs.select_passages(ARTICLE, ["heart", "hearts", "blood"])
    assert text.startswith(INTRO) and HEART in text
    assert DIET not in text
    assert "cited paper" not in text  # after the References heading


def test_select_matches_whole_words_only():
    text = rs.select_passages("\n".join([INTRO, QUICKTIME]), ["tim"])
    assert QUICKTIME not in text  # "tim" must not match "Quicktime"


def test_select_respects_budget():
    long_para = "heart " * 100
    text = rs.select_passages("\n".join([INTRO] + [long_para.strip() + f" {i}." for i in range(20)]),
                              ["heart"], budget=1500)
    assert len(text) <= 1500


def wiki_transport(extracts: dict[str, str]):
    def handler(req: httpx.Request):
        lang = req.url.host.split(".")[0]
        p = req.url.params
        assert req.headers["User-Agent"].startswith("vidgen/")
        if p.get("list") == "search":
            hits = [{"title": p["srsearch"]}] if lang in extracts else []
            return httpx.Response(200, json={"query": {"search": hits}})
        return httpx.Response(200, json={"query": {"pages": {"1": {
            "title": p["titles"], "fullurl": f"https://{lang}.wikipedia.org/wiki/{p['titles']}",
            "extract": extracts[lang]}}}})
    return httpx.Client(transport=httpx.MockTransport(handler), headers={"User-Agent": rs.USER_AGENT})


def test_research_uses_english_and_local_wikipedia():
    wiki = rs.Wikipedia(wiki_transport({"en": ARTICLE, "vi": ARTICLE}))
    sources = rs.research("Vì sao bạch tuộc có ba trái tim", "vi", LLMChain([FakeLLM(PLAN)]), wiki)
    assert [(s.lang, s.title) for s in sources] == [("en", "Octopus"), ("vi", "Bạch tuộc")]
    assert HEART in sources[0].text


def test_research_english_video_looks_up_once():
    wiki = rs.Wikipedia(wiki_transport({"en": ARTICLE}))
    sources = rs.research("octopus hearts", "en", LLMChain([FakeLLM(PLAN)]), wiki)
    assert len(sources) == 1


def test_research_degrades_to_no_sources():
    down = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert rs.research("x", "vi", LLMChain([FakeLLM(PLAN)]), rs.Wikipedia(down)) == []
    assert rs.research("x", "vi", LLMChain([FakeLLM("not json")]), rs.Wikipedia(down)) == []


def test_writer_puts_facts_in_prompt_and_records_sources():
    src = rs.Source("Octopus", "https://en.wikipedia.org/wiki/Octopus", "en", HEART)
    long_scene = {"narration": " ".join(["từ"] * 20) + ".", "visual_query": "octopus",
                  "visual_type": "stock", "ai_prompt": ""}
    fake = FakeLLM({"title": "t", "hook": "h", "scenes": [long_scene] * 12})
    script = writer.generate("x", "short", "vi", get_settings().preset("short"), LLMChain([fake]), [src])
    assert "Use ONLY facts" in fake.prompts[0] and "systemic heart stops" in fake.prompts[0]
    assert [s.url for s in script.sources] == ["https://en.wikipedia.org/wiki/Octopus"]


def test_writer_without_sources_asks_to_stay_general():
    long_scene = {"narration": " ".join(["từ"] * 20) + ".", "visual_query": "q",
                  "visual_type": "stock", "ai_prompt": ""}
    fake = FakeLLM({"title": "t", "hook": "h", "scenes": [long_scene] * 12})
    script = writer.generate("x", "short", "vi", get_settings().preset("short"), LLMChain([fake]))
    assert "No reference sources" in fake.prompts[0] and script.sources == []
