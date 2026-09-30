import json

from vidgen import pipeline
from vidgen.config import get_settings
from vidgen.models import Angle, Brief, Scene, Script, SourceDoc
from vidgen.script import writer
from vidgen.script.factcheck import fact_check
from vidgen.script.llm import LLMChain

SRC = SourceDoc(title="Octopus", url="u", lang="en", text="The systemic heart stops while the octopus swims.")
SCRIPT = Script(title="t", hook="h", lang="vi", format="short", scenes=[
    Scene(id=1, narration="Bạch tuộc có ba trái tim.", visual_query="q"),
    Scene(id=2, narration="Chỉ một tim hoạt động khi bơi.", visual_query="q"),
    Scene(id=3, narration="Bạn nghĩ sao?", visual_query="q")])


class Fake:
    name = "fake"

    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        return json.dumps(self.reply)


def test_fact_check_flags_by_scene_text():
    llm = Fake({"issues": [{"id": 2, "note": "Nguồn nói tim chính ngừng khi bơi."}, {"id": 99, "note": "x"}]})
    result = fact_check(SCRIPT, [SRC], LLMChain([llm]))
    assert result.checked and result.issues == [
        {"scene_id": 2, "narration": "Chỉ một tim hoạt động khi bơi.", "note": "Nguồn nói tim chính ngừng khi bơi."}]
    assert "systemic heart stops" in llm.prompts[0] and "2. Chỉ một tim" in llm.prompts[0]


def test_fact_check_without_sources_is_not_checked():
    llm = Fake({"issues": []})
    assert fact_check(SCRIPT, [], LLMChain([llm])).checked is False and llm.prompts == []


def test_rewrite_one_uses_neighbours_and_instruction():
    llm = Fake({"narration": "Mới.", "visual_query": "octopus swimming", "visual_type": "stock", "ai_prompt": ""})
    out = writer.rewrite_one(SCRIPT, SCRIPT.scenes[1], "Trước.", "Sau.", "ngắn hơn", [SRC], None, LLMChain([llm]))
    assert out.narration == "Mới."
    p = llm.prompts[0]
    assert "Previous scene: Trước." in p and "Next scene: Sau." in p and "ngắn hơn" in p


def test_pipeline_check_facts_writes_file(tmp_path, monkeypatch):
    brief = Brief(topic="t", angles=[Angle(style="explain", title="T", hook="H", key_points=["k"], sources=[SRC])],
                  chosen=0)
    (tmp_path / pipeline.BRIEF_FILE).write_text(brief.model_dump_json(), encoding="utf-8")
    (tmp_path / "script.json").write_text(SCRIPT.model_dump_json(), encoding="utf-8")
    llm = Fake({"issues": [{"id": 2, "note": "sai"}]})
    monkeypatch.setattr("vidgen.script.llm.default_chain", lambda s, role="creative": LLMChain([llm]))
    pipeline.check_facts(tmp_path, get_settings())
    saved = json.loads((tmp_path / "factcheck.json").read_text(encoding="utf-8"))
    assert saved["checked"] and saved["issues"][0]["scene_id"] == 2


def test_questions_are_never_flagged():
    llm = Fake({"issues": [{"id": 3, "note": "Đây là câu hỏi, bỏ qua."}]})
    assert fact_check(SCRIPT, [SRC], LLMChain([llm])).issues == []


def test_rewrite_retries_when_too_long_for_shorter_request():
    long = {"narration": " ".join(["dài"] * 32) + ".", "visual_query": "q", "visual_type": "stock", "ai_prompt": ""}
    short = {"narration": "Ngắn gọn thôi.", "visual_query": "q", "visual_type": "stock", "ai_prompt": ""}

    class Seq:
        name = "seq"

        def __init__(self):
            self.replies, self.prompts = [long, short], []

        def generate_json(self, prompt, schema):
            self.prompts.append(prompt)
            return json.dumps(self.replies.pop(0))

    llm = Seq()
    out = writer.rewrite_one(SCRIPT, SCRIPT.scenes[1], "", "", "ngắn hơn", [SRC], None, LLMChain([llm]))
    assert out.narration == "Ngắn gọn thôi." and len(llm.prompts) == 2
    assert "at most 6 words" in llm.prompts[0] or "at most 6" in llm.prompts[0]  # 7 words now → ≤6


def test_rewrite_keeps_english_visual_query():
    llm = Fake({"narration": "Mới.", "visual_query": "bạch tuộc trong hang", "visual_type": "stock", "ai_prompt": ""})
    out = writer.rewrite_one(SCRIPT, SCRIPT.scenes[0], "", "", None, [SRC], None, LLMChain([llm]))
    assert out.visual_query == "q"


def test_fact_check_batches_long_scripts():
    long_script = Script(title="t", hook="h", lang="vi", format="long",
                         scenes=[Scene(id=i, narration=f"Câu {i}.", visual_query="q") for i in range(1, 61)])
    llm = Fake({"issues": []})
    fact_check(long_script, [SRC], LLMChain([llm]))
    assert len(llm.prompts) == 3 and "60. Câu 60." in llm.prompts[2] and "26." not in llm.prompts[0]


def test_rewrite_keeps_only_english_alt_queries():
    llm = Fake({"narration": "Mới.", "visual_query": "octopus den", "alt_queries": ["hang đá", "octopus arm close up"],
                "visual_type": "stock", "ai_prompt": ""})
    out = writer.rewrite_one(SCRIPT, SCRIPT.scenes[0], "", "", None, [SRC], None, LLMChain([llm]))
    assert out.alt_queries == ["octopus arm close up"]


def test_factcheck_view_drops_flags_on_edited_sentences_and_follows_renumbering(tmp_path):
    import json

    from vidgen.models import Scene, Script
    from vidgen.web.app import current_factcheck

    (tmp_path / "factcheck.json").write_text(json.dumps({"checked": True, "issues": [
        {"scene_id": 2, "narration": "Câu đã sửa.", "note": "x"},
        {"scene_id": 5, "narration": "Câu còn nguyên.", "note": "y"}]}), encoding="utf-8")
    script = Script(title="t", hook="h", lang="vi", format="short",
                    scenes=[Scene(id=1, narration="Mở.", visual_query="q"),
                            Scene(id=2, narration="Câu còn nguyên.", visual_query="q")])
    fc = current_factcheck(tmp_path, script)
    assert fc["checked"] and fc["issues"] == [{"scene_id": 2, "narration": "Câu còn nguyên.", "note": "y"}]
    assert current_factcheck(tmp_path / "none", script) is None


def test_notes_that_confirm_a_scene_are_not_flags():
    # seen live (qwen3:8b checker): 7 of 10 "issues" were just "Đúng."
    llm = Fake({"issues": [{"id": 1, "note": "Đúng."}, {"id": 2, "note": "Chính xác, khớp tài liệu."},
                           {"id": 1, "note": "Correct."}, {"id": 2, "note": "Sai ngày: tài liệu nói năm 2016."},
                           {"id": 1, "note": "Chính phủ không được nhắc trong tài liệu."},
                           {"id": 2, "note": "Đúng, nhưng năm là 2016."}, {"id": 1, "note": "Chính xác hơn là 2 tim."},
                           {"id": 2, "note": "True, but the figure is wrong."}]})
    assert [i["note"] for i in fact_check(SCRIPT, [SRC], LLMChain([llm])).issues] == [
        "Sai ngày: tài liệu nói năm 2016.", "Chính phủ không được nhắc trong tài liệu.", "Đúng, nhưng năm là 2016.",
        "Chính xác hơn là 2 tim.", "True, but the figure is wrong."]


def test_rewriting_the_hook_keeps_it_hook_sized_and_plain():
    long = {"narration": "Imagine UN chief Kurt Waldheim and six\u2011year\u2011old Nick Sagan each sending a greeting to alien listeners.",
            "visual_query": "q", "visual_type": "stock", "ai_prompt": ""}
    short = {"narration": "A UN chief and a six\u2011year\u2011old both greeted aliens.", "visual_query": "q",
             "visual_type": "stock", "ai_prompt": ""}

    class Seq:
        name = "seq"

        def __init__(self):
            self.replies, self.prompts = [long, short], []

        def generate_json(self, prompt, schema):
            self.prompts.append(prompt)
            return json.dumps(self.replies.pop(0))
    llm = Seq()
    out = writer.rewrite_one(SCRIPT, SCRIPT.scenes[0], "", "Sau.", "make it punchier", [SRC], None, LLMChain([llm]))
    assert out.narration == "A UN chief and a six-year-old both greeted aliens." and len(llm.prompts) == 2


# --- two independent checkers: one run misses what another catches (seen live: gpt-oss let "1960" through) -----
def test_flags_from_two_checkers_are_merged_per_scene():
    from vidgen.script.factcheck import FactCheck, merge
    a = FactCheck(checked=True, issues=[{"scene_id": 2, "narration": "x", "note": "Sai năm."}])
    b = FactCheck(checked=True, issues=[{"scene_id": 2, "narration": "x", "note": "Không có trong nguồn."},
                                        {"scene_id": 3, "narration": "y", "note": "Sai tên."}])
    m = merge(a, b)
    assert m.checked and [(i["scene_id"], i["note"]) for i in m.issues] == [
        (2, "Sai năm. / Không có trong nguồn."), (3, "Sai tên.")]
    assert merge(FactCheck(checked=False), b).checked is True


def test_check_facts_runs_both_checkers_and_survives_the_second_failing(tmp_path, monkeypatch):
    brief = Brief(topic="t", angles=[Angle(style="explain", title="T", hook="H", key_points=["k"], sources=[SRC])],
                  chosen=0)
    (tmp_path / pipeline.BRIEF_FILE).write_text(brief.model_dump_json(), encoding="utf-8")
    (tmp_path / "script.json").write_text(SCRIPT.model_dump_json(), encoding="utf-8")
    first, second = Fake({"issues": [{"id": 1, "note": "Sai số."}]}), Fake({"issues": [{"id": 2, "note": "Sai năm."}]})
    roles = []

    def chain(s, role="creative"):
        roles.append(role)
        return LLMChain([first if role == "checker" else second])
    monkeypatch.setattr("vidgen.script.llm.default_chain", chain)
    settings = get_settings().model_copy(deep=True)
    settings.pipeline.llm.second_checker = ["groq:qwen/qwen3.8-27b"]
    pipeline.check_facts(tmp_path, settings)
    saved = json.loads((tmp_path / "factcheck.json").read_text(encoding="utf-8"))
    assert roles == ["checker", "second_checker"] and [i["scene_id"] for i in saved["issues"]] == [1, 2]

    def broken(s, role="creative"):
        if role == "second_checker":
            raise RuntimeError("no key")
        return LLMChain([first])
    monkeypatch.setattr("vidgen.script.llm.default_chain", broken)
    first.reply = {"issues": [{"id": 1, "note": "Sai số."}]}
    pipeline.check_facts(tmp_path, settings)
    assert [i["scene_id"] for i in json.loads((tmp_path / "factcheck.json").read_text(encoding="utf-8"))["issues"]] == [1]


def test_checkers_run_cooler_than_the_writer():
    from vidgen.config import LLMConfig
    from vidgen.script.llm import default_chain
    s = get_settings().model_copy(deep=True)
    s.pipeline.llm = LLMConfig(creative=["groq:openai/gpt-oss-120b"], checker=["groq:openai/gpt-oss-120b"],
                               second_checker=["groq:qwen/qwen3.8-27b"])
    s.secrets.groq_api_key = "k"
    assert default_chain(s).providers[0].temperature == 0.7
    assert default_chain(s, "checker").providers[0].temperature == 0.2
    assert default_chain(s, "second_checker").providers[0].temperature == 0.2


def test_a_broken_script_never_fails_the_check(tmp_path):
    """Review finding 5: check_facts promises never to fail its caller."""
    (tmp_path / "script.json").write_text("{not json", encoding="utf-8")
    pipeline.check_facts(tmp_path, get_settings())
    assert json.loads((tmp_path / "factcheck.json").read_text(encoding="utf-8"))["checked"] is False
