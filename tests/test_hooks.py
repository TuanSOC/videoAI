"""Phase 2 (retention): a real hook in the first 3 seconds and an open loop paid off near the end."""

import json

import pytest

from vidgen.config import get_settings
from vidgen.script import hooks, writer
from vidgen.script.llm import LLMChain


@pytest.mark.parametrize("text, fixed", [
    ("Bạn có biết rằng bạch tuộc có ba trái tim?", "Bạch tuộc có ba trái tim?"),
    ("Hôm nay chúng ta nói về bạch tuộc, loài có máu xanh.", "Bạch tuộc, loài có máu xanh."),
    ("Did you know that octopuses have three hearts?", "Octopuses have three hearts?"),
    ("Have you ever wondered why the sky is blue?", "Why the sky is blue?"),
])
def test_generic_openers_are_cut_off(text, fixed):
    assert hooks.strip_generic_opener(text) == fixed


def test_good_hooks_are_left_alone():
    assert hooks.strip_generic_opener("Con tàu biến mất không để lại dấu vết nào.") is None
    assert hooks.hook_problem("Con tàu biến mất không để lại dấu vết nào.") is None
    assert hooks.hook_problem(" ".join(["từ"] * 20) + ".") == "too long"
    assert hooks.hook_problem("Xin chào.") == "generic"


# --- short draft ---------------------------------------------------------------------------------------------
class Scripted:
    """Answers by response schema name, in order; records every call."""
    name = "scripted"

    def __init__(self, **by_schema):
        self.by_schema = {k: list(v) for k, v in by_schema.items()}
        self.calls: list[tuple[str, str]] = []

    def generate_json(self, prompt, schema):
        self.calls.append((schema.__name__, prompt))
        return json.dumps(self.by_schema[schema.__name__].pop(0))


def draft(hook, n=6, payoff=5, words_per_scene=22):
    body = [{"narration": " ".join([f"w{i}"] * words_per_scene) + ".", "visual_query": "q"} for i in range(n - 1)]
    body[-1]["narration"] = body[-1]["narration"][:-1] + "?"   # ends by asking the viewer, like a real draft
    return {"title": "T", "hook": hook, "mood": "mystery", "open_loop": "Vì sao?", "payoff_scene": payoff,
            "scenes": [{"narration": hook, "visual_query": "q"}, *body]}


def preset():
    return get_settings().preset("short").model_copy(update={"target_seconds": (30, 34)})


def test_generic_hook_fixed_without_an_extra_call():
    llm = Scripted(ShortDraft=[draft("Bạn có biết rằng bạch tuộc có ba trái tim?")])
    script = writer.generate("x", "short", "vi", preset(), LLMChain([llm]))
    assert script.hook == "Bạch tuộc có ba trái tim?" == script.scenes[0].narration
    assert [c[0] for c in llm.calls] == ["ShortDraft"]


def test_long_hook_gets_one_rewrite():
    long_hook = " ".join(["dài"] * 20) + "."
    llm = Scripted(ShortDraft=[draft(long_hook)], HookFix=[{"hook": "Máu bạch tuộc có màu xanh."}])
    script = writer.generate("x", "short", "vi", preset(), LLMChain([llm]))
    assert script.scenes[0].narration == "Máu bạch tuộc có màu xanh." and script.hook == script.scenes[0].narration
    assert [c[0] for c in llm.calls] == ["ShortDraft", "HookFix"]


def test_a_rewrite_that_is_still_bad_keeps_the_original_after_one_try():
    long_hook = " ".join(["dài"] * 20) + "."
    llm = Scripted(ShortDraft=[draft(long_hook)], HookFix=[{"hook": " ".join(["vẫn"] * 25) + "."}])
    script = writer.generate("x", "short", "vi", preset(), LLMChain([llm]))
    assert script.scenes[0].narration == long_hook and len(llm.calls) == 2


def test_expansion_never_goes_after_the_payoff():
    short = draft("Con tàu biến mất.", n=4, payoff=3, words_per_scene=5)
    llm = Scripted(ShortDraft=[short],
                   ExpandedScenes=[{"new_scenes": [{"after": 3, "narration": "Cảnh mới thêm vào.", "visual_query": "q"}]}])
    script = writer.generate("x", "short", "vi", preset(), LLMChain([llm]))
    texts = [s.narration for s in script.scenes]
    payoff = short["scenes"][2]["narration"]
    assert texts.index("Cảnh mới thêm vào.") < texts.index(payoff)   # the answer stays near the end


def test_prompts_ask_for_the_structure():
    prompt = writer.templates.render("short.md", topic="t", facts="", angle="", lang_name="Vietnamese", target_words=1,
                           target_seconds=1, scene_range="1", max_ai_video=0)
    for needle in ("open_loop", "payoff_scene", "second-to-last", "digits", "Bạn có biết"):
        assert needle in prompt, needle
    brief = writer.templates.render("brief_angles.md", topic="t", lang_name="Vietnamese", format_note="", facts="")
    assert "paradox" in brief and "Bạn có biết" in brief


# --- brief ---------------------------------------------------------------------------------------------------
def test_brief_hooks_lose_generic_openers():
    from vidgen.script import brief as br

    angles = [{"style": s, "title": "T", "hook": h, "key_points": ["a", "b"]} for s, h in
              [("explain", "Bạn có biết rằng bạch tuộc có ba trái tim?"), ("myth", "Máu người luôn màu đỏ?"),
               ("story", "Xin chào các bạn, năm 1995 một con tàu biến mất.")]]
    llm = Scripted(AngleList=[{"angles": angles}])
    brief = br.make_brief("x", "vi", "short", LLMChain([llm]), research=False)
    assert [a.hook for a in brief.angles] == ["Bạch tuộc có ba trái tim?", "Máu người luôn màu đỏ?",
                                              "Năm 1995 một con tàu biến mất."]


def test_angle_hook_opens_the_video_even_if_the_model_ignored_it():
    from vidgen.models import Angle
    angle = Angle(style="myth", title="T", hook="Mật ong có thể sống hàng nghìn năm?", key_points=["a"], sources=[])
    d = draft("Mật ong có độ ẩm chỉ 18%.")
    llm = Scripted(ShortDraft=[d])
    script = writer.generate("x", "short", "vi", preset(), LLMChain([llm]), [], angle)
    # hook → open question → the model's own opening (kept, not replaced)
    assert [s.narration for s in script.scenes[:3]] == [angle.hook, "Vì sao?", "Mật ong có độ ẩm chỉ 18%."]


def test_open_loop_question_is_placed_second():
    d = draft("Con tàu biến mất không dấu vết.")
    d["open_loop"] = "Vậy điều gì đã thực sự xảy ra?"
    script = writer.generate("x", "short", "vi", preset(), LLMChain([Scripted(ShortDraft=[d])]))
    assert script.scenes[1].narration == "Vậy điều gì đã thực sự xảy ra?"
    d2 = draft("Con tàu biến mất không dấu vết.")
    d2["scenes"][1]["narration"] = "Vì sao không ai tìm thấy xác tàu?"   # already a question: nothing added
    d2["open_loop"] = "Vì sao không ai tìm thấy xác tàu?"
    script2 = writer.generate("x", "short", "vi", preset(), LLMChain([Scripted(ShortDraft=[d2])]))
    assert [s.narration for s in script2.scenes].count("Vì sao không ai tìm thấy xác tàu?") == 1


def test_a_paraphrase_of_the_angle_hook_is_replaced_not_repeated():
    from vidgen.models import Angle
    angle = Angle(style="myth", title="T", hook="Mật ong có thể sống hàng trăm năm mà không hỏng?", key_points=["a"],
                  sources=[])
    d = draft("Bạn có biết mật ong có thể sống hàng trăm năm không?")
    d["open_loop"] = ""
    script = writer.generate("x", "short", "vi", preset(), LLMChain([Scripted(ShortDraft=[d])]), [], angle)
    assert script.scenes[0].narration == angle.hook
    assert "Bạn có biết" not in script.scenes[1].narration


def test_the_answer_moves_to_just_before_the_closing_question():
    d = draft("Con tàu biến mất không dấu vết.", n=8, payoff=3)
    d["open_loop"] = ""
    answer = d["scenes"][2]["narration"]
    script = writer.generate("x", "short", "vi", preset(), LLMChain([Scripted(ShortDraft=[d])]))
    texts = [s.narration for s in script.scenes]
    assert texts[-2] == answer and texts.count(answer) == 1


def test_only_the_last_scene_asks_for_comments():
    from vidgen.models import Scene

    def sc(n):
        return Scene(id=0, narration=n, visual_query="q")
    scenes = [sc("Một tên miền đã chặn WannaCry."), sc("Vì sao một chuỗi ký tự lại dừng được nó?"),
              sc("Hutchins đăng ký tên miền đó."), sc("Bạn nghĩ gì về điều này? Hãy chia sẻ!"),
              sc("Vậy câu trả lời: tên miền là công tắc tắt."), sc("Bạn sẽ làm gì? Hãy để lại bình luận.")]
    out = writer._open_loop_structure(scenes, "", None, 0)
    assert [s.narration for s in out] == [s.narration for s in scenes[:3] + scenes[4:]]
    assert hooks.is_comment_cta("Let me know in the comments!") and not hooks.is_comment_cta("Vì sao nó lây lan?")


def test_the_hook_is_never_dropped_as_a_comment_request():
    from vidgen.models import Scene
    scenes = [Scene(id=0, narration=n, visual_query="q") for n in
              ("Hãy chia sẻ nếu bạn từng mất dữ liệu vì ransomware!", "Vì sao?", "Vì chưa vá.", "Bạn nghĩ sao? Hãy chia sẻ!")]
    assert len(writer._open_loop_structure(scenes, "", None, 0)) == 4


def test_a_short_without_a_closing_question_gets_one():
    class Q:
        name, tier = "groq:x", "strong"

        def __init__(self):
            self.prompts = []

        def generate_json(self, prompt, schema):
            self.prompts.append(prompt)
            return json.dumps({"question": "Bạn nghĩ lỗ đen còn giấu bí mật gì?"})
    from vidgen.models import Scene
    scenes = [Scene(id=0, narration=n, visual_query="space") for n in ("Mở đầu.", "Vì sao?", "Vì trọng lực.")]
    llm = Q()
    out = writer.ensure_closing_question(scenes, "Lỗ đen", "vi", LLMChain([llm]))
    assert [s.narration for s in out][-1] == "Bạn nghĩ lỗ đen còn giấu bí mật gì?" and len(out) == 4
    assert writer.ensure_closing_question(out, "Lỗ đen", "vi", LLMChain([llm])) == out
    assert len(llm.prompts) == 1                                         # already ends with a question: no call


def test_closing_question_is_language_sized_and_filmed_on_its_own():
    class Q:
        name, tier = "groq:x", "strong"

        def generate_json(self, prompt, schema):
            return json.dumps({"question": "Nếu là bạn, bạn có dám bấm nút gửi thông điệp đó ra vũ trụ không?",
                               "visual_query": "person looking at night sky"})
    from vidgen.models import Scene
    scenes = [Scene(id=0, narration=n, visual_query="space probe") for n in ("Mở đầu.", "Vì sao?", "Vì trọng lực.")]
    out = writer.ensure_closing_question(scenes, "Voyager", "vi", LLMChain([Q()]))
    assert out[-1].narration.endswith("không?")                          # 16 syllables: fine in Vietnamese
    assert out[-1].visual_query == "person looking at night sky" and out[-1].alt_queries == []
