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
    """Queued responses for content calls; article-pick calls answer `pick` (default: first hit)."""
    name = "fake"

    def __init__(self, *responses, pick=0):
        self.responses = list(responses)
        self.pick = pick
        self.prompts = []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        if schema.__name__ == "ArticlePick":
            return json.dumps({"index": self.pick, "reason": "test"})
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return r if isinstance(r, str) else json.dumps(r)


PLAN = {"queries_en": ["Octopus"], "queries_local": ["Bạch tuộc"], "keywords": ["heart", "hearts", "tim"]}


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
    src = rs.Source(title="Octopus", url="https://en.wikipedia.org/wiki/Octopus", lang="en", text=HEART)
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


def search_transport(hits_by_lang: dict[str, list[tuple[str, str]]], extract: str = ARTICLE):
    """Search returns the given (title, snippet) hits; any extract request returns `extract`."""
    def handler(req: httpx.Request):
        lang, p = req.url.host.split(".")[0], req.url.params
        if p.get("list") == "search":
            assert p["srlimit"] == "5"
            hits = [{"title": t, "snippet": f'<span class="searchmatch">{sn}</span>'}
                    for t, sn in hits_by_lang.get(lang, [])]
            return httpx.Response(200, json={"query": {"search": hits}})
        return httpx.Response(200, json={"query": {"pages": {"1": {
            "title": p["titles"], "fullurl": f"https://{lang}.wikipedia.org/wiki/{p['titles']}",
            "extract": extract}}}})
    return httpx.Client(transport=httpx.MockTransport(handler))


SALT_HITS = [("Why the Sea is Salt", "Norwegian fairy tale about a magic mill"),
             ("Saltwater crocodile", "largest living reptile"),
             ("Seawater", "water from a sea or ocean; salinity about 3.5%")]


def test_pick_skips_namesakes_and_uses_llm_choice():
    llm = LLMChain([FakeLLM(pick=2)])
    wiki = rs.Wikipedia(search_transport({"en": SALT_HITS}))
    sources = rs.lookup_sources("why the sea is salty", "en", ["sea salt"], [], ["salt"], llm, wiki)
    assert [s.title for s in sources] == ["Seawater"]
    pick_prompt = [p for p in llm.providers[0].prompts if "Candidates:" in p][0]
    assert "fairy tale" in pick_prompt and "<span" not in pick_prompt  # snippet HTML stripped


def test_pick_none_means_no_source():
    wiki = rs.Wikipedia(search_transport({"en": SALT_HITS}))
    assert rs.lookup_sources("x", "en", ["q"], [], [], LLMChain([FakeLLM(pick=-1)]), wiki) == []


def test_pick_out_of_range_means_no_source():
    wiki = rs.Wikipedia(search_transport({"en": SALT_HITS}))
    assert rs.lookup_sources("x", "en", ["q"], [], [], LLMChain([FakeLLM(pick=9)]), wiki) == []


def test_lookup_cache_shared_across_calls():
    calls = []
    base = search_transport({"en": [("Octopus", "cephalopod")], "vi": [("Bạch tuộc", "động vật")]})

    def counting(req):
        calls.append(req.url.params.get("list") or "extract")
        return base._transport.handle_request(req)

    wiki = rs.Wikipedia(httpx.Client(transport=httpx.MockTransport(counting)))
    cache: dict = {}
    llm = LLMChain([FakeLLM()])
    a = rs.lookup_sources("t", "vi", ["Octopus"], ["Bạch tuộc"], ["heart"], llm, wiki, cache)
    b = rs.lookup_sources("t", "vi", ["octopus "], ["Bạch tuộc"], ["heart"], llm, wiki, cache)
    assert len(a) == len(b) == 2 and len(calls) == 4  # 2 searches + 2 extracts, second call all cached


def test_hits_pooled_across_queries_and_deduped():
    seen = []

    def handler(req):
        p = req.url.params
        if p.get("list") == "search":
            seen.append(p["srsearch"])
            hits = {"Seawater": [("Seawater", "a"), ("Ocean", "b")],
                    "Salinity": [("Salinity", "c"), ("Seawater", "a")]}[p["srsearch"]]
            return httpx.Response(200, json={"query": {"search": [{"title": t, "snippet": s} for t, s in hits]}})
        return httpx.Response(200, json={"query": {"pages": {"1": {"title": p["titles"], "fullurl": "u",
                                                                   "extract": ARTICLE}}}})

    llm = FakeLLM(pick=2)
    wiki = rs.Wikipedia(httpx.Client(transport=httpx.MockTransport(handler)))
    [src] = rs.lookup_sources("t", "en", ["Seawater", "Salinity"], [], ["heart"], LLMChain([llm]), wiki)
    assert seen == ["Seawater", "Salinity"] and src.title == "Salinity"
    prompt = [p for p in llm.prompts if "Candidates:" in p][0]
    listing = prompt.split("Candidates:")[1].split("Rules:")[0]
    assert listing.count("Seawater") == 1  # duplicate hit shown once


def test_rate_limit_waits_retry_after_then_succeeds():
    calls, waits = [], []

    def handler(req):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"Retry-After": "9"}, text="too many")
        return httpx.Response(200, json={"query": {"search": [{"title": "Octopus", "snippet": ""}]}})

    wiki = rs.Wikipedia(httpx.Client(transport=httpx.MockTransport(handler)), sleep=waits.append)
    assert [h.title for h in wiki.search("en", "Octopus")] == ["Octopus"]
    assert waits == [9.0, 9.0]


def test_rate_limit_gives_up_after_retries():
    wiki = rs.Wikipedia(httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(429, headers={"Retry-After": "100"}))), sleep=lambda s: None)
    import pytest
    with pytest.raises(httpx.HTTPStatusError):
        wiki.search("en", "x")


def test_disk_cache_avoids_repeat_requests(tmp_path):
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(200, json={"query": {"search": [{"title": "Bạch tuộc", "snippet": ""}]}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    rs.Wikipedia(client, cache_dir=tmp_path).search("vi", "bạch tuộc")
    again = rs.Wikipedia(client, cache_dir=tmp_path).search("vi", "bạch tuộc")
    assert again[0].title == "Bạch tuộc" and len(calls) == 1
