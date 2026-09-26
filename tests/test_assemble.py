import pytest

from vidgen.assemble import subtitles as subs
from vidgen.assemble.clips import Shot, frame_counts, plan_shots, shot_args
from vidgen.assemble.focus import Focus
from vidgen.assemble.render import audio_filter, final_args
from vidgen.config import get_settings
from vidgen.metadata import credit_lines
from vidgen.models import Asset, Scene, SceneAudio, Script, Timeline, WordTiming


def w(word, start, end):
    return WordTiming(word=word, start=start, end=end)


def make(fmt="short"):
    script = Script(title="t", hook="h", lang="vi", format=fmt, scenes=[
        Scene(id=1, narration="Bí ẩn tam giác.", visual_query="q"),
        Scene(id=2, narration="Sự thật đơn giản", visual_query="q")])
    tl = Timeline(scenes=[
        SceneAudio(scene_id=1, path="a", start=0.0, duration=1.5,
                   words=[w("Bí", 0.1, 0.3), w("ẩn", 0.3, 0.5), w("tam", 0.5, 0.7), w("giác", 0.7, 1.0)]),
        SceneAudio(scene_id=2, path="b", start=1.5, duration=1.2,
                   words=[w("Sự", 1.6, 1.8), w("thật", 1.8, 2.0), w("đơn", 2.0, 2.2), w("giản", 2.2, 2.5)]),
    ])
    return script, tl


def test_ass_time():
    assert subs.ass_time(0) == "0:00:00.00"
    assert subs.ass_time(3725.456) == "1:02:05.46"


def test_display_words_restores_punctuation():
    script, tl = make()
    words = subs.display_words(script, tl)
    assert words[3].word == "giác."
    assert words[3].start == 0.7


def test_display_words_falls_back_when_token_count_differs():
    script, tl = make()
    script.scenes[0].narration = "Bí ẩn"  # 2 tokens vs 4 timings
    assert subs.display_words(script, tl)[0].word == "Bí"


def test_chunk_breaks_on_max_punct_and_pause():
    words = [w("a", 0, .1), w("b.", .1, .2), w("c", .2, .3), w("d", .3, .4), w("e", .4, .5),
             w("f", .5, .6), w("g", 1.5, 1.6)]
    chunks = subs.chunk_words(words, 3)
    assert [[x.word for x in c] for c in chunks] == [["a", "b."], ["c", "d", "e"], ["f"], ["g"]]


def test_short_ass_one_line_word_highlight_without_effects():
    script, tl = make("short")
    ass = subs.build_ass(script, tl, get_settings().preset("short"))
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    # "Bí ẩn tam giác." is one clean phrase, "Sự thật đơn giản" another
    assert len(dialogues) == 2 and "giác." in dialogues[0] and "Bí" in dialogues[0]
    assert dialogues[0].count(r"{\k") == 4 and r"\kf" not in ass  # spoken word turns yellow at once
    assert r"{\k20}Bí" in dialogues[0]                            # 0.1 → 0.3 s = 20 cs
    assert r"\t(" not in ass and r"\fscx" not in ass             # no pop-in, no zoomed keywords
    assert r"\N" not in ass                                      # one line
    assert "PlayResY: 1920" in ass and ",110,210,620," in ass    # safe-zone margins


def test_long_ass_one_event_per_chunk():
    script, tl = make("long")
    ass = subs.build_ass(script, tl, get_settings().preset("long"))
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert len(dialogues) == 2
    assert "giác." in dialogues[0] and subs.HIGHLIGHT not in ass


def test_frame_counts_do_not_accumulate_rounding():
    tl = Timeline(scenes=[SceneAudio(scene_id=i, path="", start=i * 1.01, duration=1.01, words=[])
                          for i in range(100)])
    counts = frame_counts(tl, 30)
    assert sum(counts) == round(tl.duration * 30)


def shots_for(durations, assets, clip_seconds, fmt="short"):
    from pathlib import Path

    t, scenes = 0.0, []
    for i, d in enumerate(durations, 1):
        scenes.append(SceneAudio(scene_id=i, path="", start=t, duration=d, words=[]))
        t += d
    return plan_shots(Timeline(scenes=scenes), assets, get_settings().preset(fmt), fmt, Path("o"),
                      {Path("o") / k: v for k, v in clip_seconds.items()})


def test_plan_shots_splits_long_scenes_and_keeps_frames():
    assets = [Asset(scene_id=1, path="v1.mp4", kind="video", source="pexels"),
              Asset(scene_id=2, path="v2.mp4", kind="video", source="pexels", extra=["v2b.mp4"]),
              Asset(scene_id=3, path="", kind="color", source="placeholder")]
    shots = shots_for([2.0, 8.0, 3.0], assets, {"v1.mp4": 10.0, "v2.mp4": 20.0, "v2b.mp4": 6.0})
    fps = get_settings().preset("short").fps
    assert sum(s.frames for s in shots) == round(13.0 * fps)            # no drift from splitting
    assert [s.scene_id for s in shots] == [1, 2, 2, 2, 3]                # 8 s → 3 shots of ≤3.5 s
    s2 = [s for s in shots if s.scene_id == 2]
    assert [s.src.name for s in s2] == ["v2.mp4", "v2b.mp4", "v2.mp4"]  # the extra clip is cut in
    assert s2[0].offset < s2[2].offset                                   # same clip, different part
    assert s2[0].punch == 0 and s2[2].punch > 0                          # ...and framed tighter
    assert shots[0].offset >= 0.6                                        # lead-in skipped
    assert s2[0].transition_in and not shots[-1].transition_in           # no fade into a placeholder
    assert all(s.motion == "none" for s in s2)                           # stock video moves by itself


def test_plan_shots_short_clip_starts_at_zero():
    shots = shots_for([3.0], [Asset(scene_id=1, path="v.mp4", kind="video", source="pexels")], {"v.mp4": 3.1})
    assert len(shots) == 1 and shots[0].offset == 0.0


def test_shot_args_short_dissolve_grade_and_grain(tmp_path):
    from pathlib import Path

    p = get_settings().preset("short")
    tf = round(0.18 * p.fps)
    a = Shot(1, 0, "video", Path("a.mp4"), 1.0, 90, "none", focus=Focus(0.3, 0.05))
    b = Shot(2, 0, "image", Path("b.jpg"), 0.0, 90, "pan_l", transition_in=True)
    args = shot_args(a, b, p, tmp_path / "o.mp4")
    graph = args[args.index("-filter_complex") + 1]
    assert f"xfade=transition=fade:duration={tf / p.fps:.4f}" in graph and args.count("-i") == 2
    assert f"offset={(90 - tf) / p.fps:.3f}" in graph
    assert "x=(iw-ow)*0.300" in graph and "brightness=0.050" in graph and "vignette" in graph
    assert graph.count("noise=alls=") == 2                               # grain on both inputs
    assert args[args.index("-frames:v") + 1] == "90"
    video_chain = graph.split(";")[0]
    assert "zoompan" not in video_chain                                  # no fake camera move on video
    assert "zoompan" in graph.split(";")[1]                              # still images keep Ken Burns
    # the incoming image continues where the fade left off: motion already started
    b_args = shot_args(b, None, p, tmp_path / "o.mp4")
    assert "xfade" not in " ".join(b_args) and f"(on+{tf})" in " ".join(b_args)
    color = shot_args(Shot(3, 0, "color", None, 0.0, 30, "none"), None, p, tmp_path / "o.mp4")
    assert any(x.startswith("color=") for x in color)


def test_punch_in_video_is_a_tighter_static_frame(tmp_path):
    from pathlib import Path

    p = get_settings().preset("short")
    plain = shot_args(Shot(1, 0, "video", Path("a.mp4"), 0, 60, "none"), None, p, tmp_path / "o.mp4")
    punch = shot_args(Shot(1, 1, "video", Path("a.mp4"), 3, 60, "none", punch=0.22), None, p, tmp_path / "o.mp4")
    g1, g2 = (x[x.index("-filter_complex") + 1] for x in (plain, punch))
    assert f"scale={p.width}:{p.height}:" in g1
    assert f"scale={round(p.width * 1.22 / 2) * 2}:{round(p.height * 1.22 / 2) * 2}:" in g2
    assert "zoompan" not in g2


def test_focus_finds_subject_and_brightness(tmp_path):
    import cv2
    import numpy as np

    from vidgen.assemble.focus import analyse, best_window

    assert best_window(np.array([0, 0, 0, 5, 5, 0.0]), 1 / 3) == 0.75
    img = np.full((360, 640, 3), 20, np.uint8)                   # dark landscape frame
    cv2.rectangle(img, (500, 100), (620, 300), (255, 255, 255), 3)  # detail near the right edge
    path = tmp_path / "ảnh.jpg"                                   # unicode path
    cv2.imencode(".jpg", img)[1].tofile(str(path))
    f = analyse(path, "image", 0, 1, 9 / 16)
    assert f.x > 0.6 and f.brightness > 0
    assert analyse(tmp_path / "missing.mp4", "video", 0, 1, 9 / 16) == Focus()


@pytest.mark.parametrize("music", [None, "m.mp3"])
def test_final_args(music):
    from pathlib import Path

    args = final_args(12.3456, Path(music) if music else None)
    assert args[-1] == "final.mp4" and "12.346" in args
    graphs = [args[i + 1] for i, a in enumerate(args) if a == "-filter_complex"]
    assert len(graphs) == 2 and "ass=" in graphs[0] and "[0:v]" not in graphs[1]  # video/audio kept apart
    assert ("sidechaincompress" in graphs[1]) == bool(music)
    assert audio_filter(False).count("[a]") == 1


def test_generate_metadata_appends_disclosure_and_credits():
    import json

    from vidgen.metadata import generate_metadata
    from vidgen.script.llm import LLMChain

    class Fake:
        name = "fake"

        def generate_json(self, prompt, schema):
            assert "#shorts" in prompt
            return json.dumps({"title": "Bí ẩn", "description": "Mô tả.", "tags": ["bermuda"],
                               "hashtags": ["#bermuda", "#shorts"]})

    script, _ = make("short")
    assets = [Asset(scene_id=1, path="", kind="video", source="pexels", url="u1", author="Ann"),
              Asset(scene_id=2, path="", kind="image", source="flux")]
    meta = generate_metadata(script, assets, LLMChain([Fake()]))
    assert meta.ai_visuals_used is True
    assert "giọng đọc AI" in meta.description and "Pexels by Ann: u1" in meta.description
    assert meta.description.rstrip().endswith("#bermuda #shorts")


def test_display_words_rejects_coincidental_count_match():
    script, tl = make()
    script.scenes[0].narration = "Một hai ba bốn."  # 4 tokens, different words
    assert subs.display_words(script, tl)[0].word == "Bí"


def test_long_chunk_avoids_one_word_orphan():
    words = [w(f"w{i}", i * .2, i * .2 + .15) for i in range(12)] + [w("end.", 2.4, 2.6)]
    chunks = subs.chunk_words(words, 12, orphan_lookahead=2)
    assert len(chunks) == 1 and chunks[0][-1].word == "end."


def test_two_lines_balances_by_characters():
    assert subs.two_lines(["a", "b", "c", "extraordinarily"]) == "a b c extraordinarily"  # fits one line
    words = ["The", "octopus", "has", "three", "hearts", "that", "pump", "blue", "blood", "everywhere"]
    assert subs.two_lines(words) == r"The octopus has three hearts\Nthat pump blue blood everywhere"


# --- smart phrasing & emphasis -----------------------------------------------------------------------
def ws(*tokens):
    return [w(tok, i * 0.3, i * 0.3 + 0.25) for i, tok in enumerate(tokens)]


def test_chunk_never_ends_on_function_word_or_splits_number_and_unit():
    texts = [[x.word for x in c] for c in subs.chunk_words(ws("Bạch", "tuộc", "có", "ba", "trái", "tim", "khỏe."), 3)]
    assert all(c[-1] != "có" for c in texts)
    texts = [" ".join(x.word for x in c) for c in subs.chunk_words(ws("Mạng", "botnet", "1.5", "triệu", "máy."), 3)]
    assert any("1.5 triệu" in t for t in texts)


def test_emphasis_numbers_names_and_topic_words():
    script = Script(title="Bạch tuộc thông minh", hook="h", lang="vi", format="short", scenes=[
        Scene(id=1, narration="Bạch tuộc có 3 trái tim.", visual_query="q"),
        Scene(id=2, narration="Người ta thấy bạch tuộc ở Nhật Bản.", visual_query="q"),
        Scene(id=3, narration="Bạch tuộc rất thông minh.", visual_query="q")])
    em = subs.emphasis_words(script)
    assert {"3", "bạch", "tuộc", "nhật", "bản"} <= em
    assert "có" not in em and "người" not in em   # function word / sentence start
    assert "thông" not in em                      # in the title but said only once


def test_short_emphasis_markup_and_long_highlight():
    script = Script(title="t", hook="h", lang="vi", format="short",
                    scenes=[Scene(id=1, narration="Có 3 tim.", visual_query="q")])
    tl = Timeline(scenes=[SceneAudio(scene_id=1, path="a", start=0, duration=1,
                                     words=[w("Có", 0, .2), w("3", .2, .4), w("tim", .4, .6)])])
    ass = subs.build_ass(script, tl, get_settings().preset("short"))
    assert r"\2c" not in ass and r"\fscx" not in ass           # short: plain words, no keyword styling
    long_ass = subs.build_ass(script.model_copy(update={"format": "long"}), tl, get_settings().preset("long"))
    assert subs.HIGHLIGHT + "3" in long_ass


def test_short_chunks_are_3_to_6_words_within_26_chars():
    text = ("Hacker tạo email giả mạo để gửi link hoặc file đính kèm chứa mã độc. "
            "Phishing là kỹ thuật lừa đảo giả mạo để lấy thông tin nhạy cảm. Cẩn thận.")
    chunks = [[x.word for x in c] for c in subs.short_chunks(ws(*text.split()))]
    assert chunks[-1] == ["Cẩn", "thận."]                       # a 2-word sentence stays whole
    # compound words repeated in the script ("mật khẩu") are never split across two phrases
    words = ws(*"Hãy kiểm tra kỹ trước khi nhập mật khẩu vào bất kỳ trang nào.".split())
    glued = [[x.word for x in c] for c in subs.short_chunks(words, glue={("mật", "khẩu")})]
    assert not any(c[-1] == "mật" for c in glued), glued
    script = Script(title="t", hook="h", lang="vi", format="short", scenes=[
        Scene(id=1, narration="Đổi mật khẩu. Mật khẩu mạnh. Ba mật khẩu.", visual_query="q")])
    assert ("mật", "khẩu") in subs.compound_pairs(script) and ("khẩu", "mạnh") not in subs.compound_pairs(script)
    # said once, still one word: a Vietnamese word segmenter knows "tài khoản", "địa chỉ", "mã độc"
    once = Script(title="t", hook="h", lang="vi", format="short", scenes=[
        Scene(id=1, narration="Hacker đánh cắp tài khoản và địa chỉ email.", visual_query="q")])
    assert {("tài", "khoản"), ("địa", "chỉ")} <= subs.compound_pairs(once)
    merged = Script(title="t", hook="h", lang="vi", format="short", scenes=[
        Scene(id=1, narration="Hacker thực hiện hành động trái phép.", visual_query="q")])
    assert ("hiện", "hành") not in subs.compound_pairs(merged)   # two words, breakable between them
    assert subs.compound_pairs(once.model_copy(update={"lang": "en"})) == set()
    for c in chunks[:-1]:
        assert 3 <= len(c) <= 6, c
        assert len(" ".join(c)) <= 26, c
        assert subs._bare(c[-1]) not in subs.FUNCTION_WORDS, c


def test_normalize_hashtags():
    from vidgen.metadata import normalize_hashtags

    assert normalize_hashtags(["bermuda", "#Bí ẩn", "#bermuda", "#"], short=True) == \
        ["#bermuda", "#Bíẩn", "#shorts"]


def test_credit_lines_dedupe_and_skip_ai():
    assets = [Asset(scene_id=1, path="", kind="video", source="pexels", url="u1", author="Ann"),
              Asset(scene_id=2, path="", kind="video", source="pexels", url="u1", author="Ann"),
              Asset(scene_id=3, path="", kind="image", source="flux")]
    assert credit_lines(assets) == ["Pexels by Ann: u1"]


# --- mood music -------------------------------------------------------------------------------------
def test_pick_music_prefers_mood_folder(tmp_path):
    from vidgen.assemble.music import pick_music

    (tmp_path / "tense").mkdir()
    (tmp_path / "calm").mkdir()
    (tmp_path / "tense" / "a.mp3").write_bytes(b"x")
    (tmp_path / "calm" / "b.mp3").write_bytes(b"x")
    assert pick_music(tmp_path, "seed", "tense").name == "a.mp3"
    assert pick_music(tmp_path, "seed", "upbeat").name in {"a.mp3", "b.mp3"}   # empty mood → any track
    assert pick_music(tmp_path, "seed", "calm") == pick_music(tmp_path, "seed", "calm")  # deterministic
    assert pick_music(tmp_path / "none", "seed", "calm") is None


def test_music_start_fits_track_and_is_deterministic():
    from vidgen.assemble.music import music_start

    assert music_start(30.0, 60.0, "s") == 0.0                  # shorter track: from the top (it loops)
    s = music_start(180.0, 60.0, "s")
    assert 0.0 <= s <= 180.0 - 60.0 - 1 and s == music_start(180.0, 60.0, "s")


def test_final_args_music_offset_and_fades():
    from pathlib import Path

    args = final_args(60.0, Path("m.mp3"), music_start=12.5)
    i = args.index("m.mp3")
    assert args[i - 5:i - 3] == ["-ss", "12.50"]
    audio = [args[k + 1] for k, a in enumerate(args) if a == "-filter_complex"][1]
    assert "afade=t=in" in audio and "afade=t=out:st=58.00" in audio


def test_script_mood_normalised():
    from vidgen.script.writer import normalize_mood

    assert normalize_mood(" Tense ") == "tense" and normalize_mood("angry") == ""


def test_sentence_end_inside_quotes():
    chunks = [[x.word for x in c] for c in subs.short_chunks(ws(*"Hỏi: 'Email này có thật không?' Rồi kiểm tra lại.".split()))]
    k = next(i for i, c in enumerate(chunks) if c[-1] == "không?'")   # the quoted question ends a phrase
    assert chunks[k + 1][0] == "Rồi" and chunks[-1][-1] == "lại."


def test_broken_segmenter_falls_back_to_repeated_pairs(monkeypatch):
    def boom(text):
        raise RuntimeError("model file corrupt")
    monkeypatch.setattr(subs, "_vi_tokenizer", lambda: boom)
    script = Script(title="t", hook="h", lang="vi", format="short", scenes=[
        Scene(id=1, narration="Đổi mật khẩu. Mật khẩu mạnh.", visual_query="q")])
    assert subs.compound_pairs(script) == {("mật", "khẩu")}


def test_music_credit_sidecar_and_description(tmp_path):
    from vidgen.assemble.music import track_credit
    from vidgen.metadata import Metadata, rebuild_description

    track = tmp_path / "Hitman.mp3"
    track.write_bytes(b"x")
    assert track_credit(track) == ""                                    # no sidecar: nothing to credit
    (tmp_path / "Hitman.credit.txt").write_text("Hitman Kevin MacLeod (incompetech.com)\n", encoding="utf-8")
    assert track_credit(track) == "Hitman Kevin MacLeod (incompetech.com)"

    script, _ = make("short")
    old = Metadata(title="t", description="Tóm tắt.\n\nx", tags=[], hashtags=["#shorts"], credits=[],
                   ai_disclosure="x", ai_visuals_used=False, summary="Tóm tắt.")
    meta = rebuild_description(old, script, [], music_credit="Hitman Kevin MacLeod (incompetech.com)")
    assert "Music:\n- Hitman Kevin MacLeod (incompetech.com)" in meta.description
    assert meta.description.rstrip().endswith("#shorts")
    assert "Music:" not in rebuild_description(old, script, []).description


def test_english_phrases_no_lone_words_or_dangling_conjunctions():
    text = ("Ransomware encrypts or steals data, holding it hostage until a ransom is paid. "
            "Attackers often use cryptocurrency to avoid being traced, making prosecution harder. "
            "Free decryption tools exist for specific ransomware strains, but success is not guaranteed.")
    chunks = [[x.word for x in c] for c in subs.short_chunks(ws(*text.split()))]
    assert all(len(c) >= 2 for c in chunks), chunks                      # never one word alone
    ends = [subs._bare(c[-1]) for c in chunks if not subs._ends(c[-1], subs.SENTENCE_END)]
    # (", but" stays at a line end once: every other split of that sentence breaks the 26-char line)
    assert not {"until", "to", "or"} & set(ends) and ends.count("but") <= 1, chunks
    assert all(len(" ".join(c)) <= subs.SHORT_HARD_CHARS for c in chunks)


def test_long_words_get_a_slightly_smaller_line_instead_of_a_lone_word():
    for text in ("Attackers often use cryptocurrency like Bitcoin to obscure their identity.",
                 "Offline backups and append-only permissions help protect against attacks."):
        chunks = [[x.word for x in c] for c in subs.short_chunks(ws(*text.split()))]
        assert all(len(c) >= 2 for c in chunks), chunks
        assert all(len(" ".join(c)) <= subs.SHORT_HARD_CHARS for c in chunks)
    script = Script(title="t", hook="h", lang="en", format="short", scenes=[
        Scene(id=1, narration="Attackers often use cryptocurrency like Bitcoin to obscure their identity.",
              visual_query="q")])
    words = ws(*script.scenes[0].narration.split())
    tl = Timeline(scenes=[SceneAudio(scene_id=1, path="a", start=0, duration=words[-1].end + .2, words=words)])
    ass = subs.build_ass(script, tl, get_settings().preset("short"))
    long_line = next(l for l in ass.splitlines() if "cryptocurrency" in l)
    assert r"{\fs" in long_line                      # shrunk to stay on one line
    assert r"{\fs" not in next(l for l in ass.splitlines() if "Attackers" in l and l.startswith("Dialogue"))
