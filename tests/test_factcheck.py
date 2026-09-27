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
    monkeypatch.setattr("vidgen.script.llm.default_chain", lambda s: LLMChain([llm]))
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
