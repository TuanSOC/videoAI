"""Quality fixes from the full review: shared text helpers, TTS alignment, sound design timing and level,
script grounding, clip-choice budget."""

import shutil
import unicodedata

import pytest

from vidgen import text
from vidgen.config import get_settings
from vidgen.models import Scene, SceneAudio, Script, Timeline, WordTiming

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def w(word, start, end):
    return WordTiming(word=word, start=start, end=end)


# --- shared text helpers -------------------------------------------------------------------------------
def test_bare_is_normalisation_proof():
    nfd = unicodedata.normalize("NFD", "Được")
    assert text.bare(nfd) == text.bare("Được") == "được"


def test_ends_with_looks_past_closers():
    assert text.ends_with("không?'") and text.ends_with("(xong.)") and text.ends_with("vậy…")
    assert not text.ends_with("vậy,") and text.ends_with("vậy,", text.CLAUSE_END)


def test_align_handles_merges_splits_and_mismatch():
    assert text.align(["năm", "2013,", "hacker"], ["năm 2013", "hacker"]) == [([0, 1], [0]), ([2], [1])]
    assert text.align(["AI-generated."], ["AI", "generated"]) == [([0], [0, 1])]
    assert text.align(["một", "hai"], ["một", "ba"]) is None
    assert text.align(["a", "—", "b"], ["a", ",", "b"]) == [([0], [0]), ([2], [2])]   # punctuation ignored


def test_every_module_uses_the_shared_bare():
    from vidgen.assemble import sfx, subtitles
    from vidgen.voice import builder
    nfd = unicodedata.normalize("NFD", "để")
    assert text.bare(nfd) in subtitles.FUNCTION_WORDS
    assert not hasattr(subtitles, "_bare") and not hasattr(builder, "_bare") and not hasattr(sfx, "_bare")


# --- TTS alignment -------------------------------------------------------------------------------------
def test_split_by_scene_accepts_a_boundary_covering_two_tokens():
    from vidgen.voice.builder import split_by_scene
    scenes = [Scene(id=1, narration="Từ năm 2013, hacker", visual_query="q"),
              Scene(id=2, narration="tấn công.", visual_query="q")]
    words = [w("Từ", 0, .2), w("năm 2013", .2, .8), w("hacker", .8, 1), w("tấn", 1.2, 1.4), w("công", 1.4, 1.6)]
    split = split_by_scene(scenes, words)
    assert [[x.word for x in s] for s in split] == [["Từ", "năm 2013", "hacker"], ["tấn", "công"]]
    across = [w("Từ", 0, .2), w("năm", .2, .4), w("2013", .4, .6), w("hacker tấn", .6, 1.2), w("công", 1.2, 1.4)]
    assert split_by_scene(scenes, across) is None          # a boundary spanning two scenes can't be cut


def test_display_words_keep_punctuation_when_tts_merges_tokens():
    from vidgen.assemble.subtitles import display_words
    script = Script(title="t", hook="h", lang="vi", format="short",
                    scenes=[Scene(id=1, narration="Từ năm 2013, hacker tấn công.", visual_query="q")])
    tl = Timeline(scenes=[SceneAudio(scene_id=1, path="", start=0, duration=2, words=[
        w("Từ", 0, .2), w("năm 2013", .2, .8), w("hacker", .9, 1.1), w("tấn", 1.1, 1.3), w("công", 1.3, 1.6)])])
    out = display_words(script, tl)
    assert [x.word for x in out] == ["Từ", "năm 2013,", "hacker", "tấn", "công."]
    assert out[1].start == .2 and out[1].end == .8


def test_tts_timings_include_the_mp3_decoder_delay(monkeypatch, tmp_path):
    import asyncio

    from vidgen.voice import tts

    class Fake:
        def __init__(self, *a, **k):
            pass

        async def stream(self):
            yield {"type": "WordBoundary", "offset": 1_000_000, "duration": 2_000_000, "text": "Chào"}
            yield {"type": "audio", "data": b"mp3"}

    monkeypatch.setattr(tts.edge_tts, "Communicate", Fake)
    [word] = asyncio.run(tts.synth_edge("Chào", "v", tmp_path / "a.mp3"))
    assert word.start == pytest.approx(0.1 + tts.MP3_DELAY) and word.end == pytest.approx(0.3 + tts.MP3_DELAY)


# --- sound design -----------------------------------------------------------------------------------------
@needs_ffmpeg
def test_synth_sounds_are_normalised_and_old_ones_replaced(tmp_path):
    from vidgen import ffmpeg
    from vidgen.assemble import sfx
    old = tmp_path / sfx.FOLDERS["pop"] / "synth_pop_1.wav"          # quiet file from the first version
    old.parent.mkdir(parents=True)
    old.write_bytes(b"old")
    lib = sfx.ensure_library(tmp_path)
    assert not old.exists()
    for kind, paths in lib.items():
        for p in paths:
            peak = ffmpeg.volume_stats(p)[1]
            assert -4.5 <= peak <= -2.0, (kind, p.name, peak)


@needs_ffmpeg
def test_sound_peak_lands_on_the_cue(tmp_path):
    import numpy as np

    from vidgen.assemble import sfx
    lib = sfx.ensure_library(tmp_path / "lib")
    out = sfx.build_sfx_track([sfx.Cue("whoosh", 2.0, 1)], lib, 4.0, tmp_path / "t.wav")
    env = sfx.envelope(out)
    assert abs(np.argmax(env) / sfx.ENVELOPE_RATE - 2.0) < 0.06


def test_whoosh_peaks_in_the_middle_of_the_dissolve():
    from vidgen.assemble import clips, sfx
    script = Script(title="t", hook="h", lang="vi", format="short",
                    scenes=[Scene(id=1, narration="một hai ba", visual_query="q")])
    tl = Timeline(scenes=[SceneAudio(scene_id=1, path="", start=0, duration=20, words=[w("một", 5, 5.2)])])
    [cue] = [c for c in sfx.detect_cues(script, tl, [10.0]) if c.kind == "whoosh"]
    assert cue.time == pytest.approx(10.0 - clips.TRANSITION / 2)


@needs_ffmpeg
def test_hundreds_of_cues_and_bad_user_files(tmp_path):
    from vidgen import ffmpeg
    from vidgen.assemble import sfx
    lib_root = tmp_path / "lib"
    lib = sfx.ensure_library(lib_root)
    broken = lib_root / sfx.FOLDERS["impact"] / "broken.mp3"
    broken.write_bytes(b"not audio")
    good = lib_root / sfx.FOLDERS["impact"] / "mine.wav"
    shutil.copy(lib["impact"][0], good)
    lib = sfx.ensure_library(lib_root)
    assert lib["impact"] == [good]                               # the broken file is skipped
    cues = [sfx.Cue("pop", 0.5 + i * 0.4, i) for i in range(300)]
    out = sfx.build_sfx_track(cues, lib, 125.0, tmp_path / "many.wav")
    assert ffmpeg.duration(out) == pytest.approx(125.0, abs=0.05)


# --- music level --------------------------------------------------------------------------------------------
def test_music_gain_is_clamped_and_silence_is_unmeasurable():
    from vidgen.assemble.render import FALLBACK_MUSIC_DB, music_gain_db
    assert music_gain_db(-20.0, -91.0) == FALLBACK_MUSIC_DB          # a silent track: no +53 dB boost
    assert music_gain_db(-20.0, float("-inf")) == FALLBACK_MUSIC_DB
    assert music_gain_db(-5.0, -60.0) <= 6.0


# --- long-format emphasis -------------------------------------------------------------------------------------
def test_long_highlight_reaches_the_second_line():
    from vidgen.assemble import subtitles as subs
    words = [w(t, i * .3, i * .3 + .25) for i, t in enumerate(
        "Một cuộc tấn công lớn nhắm vào ransomware hệ thống ngân hàng quốc gia".split())]
    [(_, _, line)] = subs._events_long([words], {"ransomware"})
    assert r"\N" + subs.HIGHLIGHT + "ransomware" in line          # the first word of line 2


# --- script grounding ----------------------------------------------------------------------------------------
def test_multi_word_keywords_select_their_paragraph():
    from vidgen.script.research import select_passages
    intro = "Mở đầu " * 30
    target = "Hệ tuần hoàn của bạch tuộc có ba trái tim bơm máu xanh đi khắp cơ thể " * 3
    other = "Một đoạn văn khác nói về chuyện hoàn toàn không liên quan gì cả " * 3
    out = select_passages("\n".join([intro, other, target]), ["tuần hoàn", "trái tim"], budget=len(intro) + len(target) + 10)
    assert "tuần hoàn" in out and "không liên quan" not in out


def test_only_the_opening_prompt_gets_the_hook_instruction():
    import json

    from vidgen.models import Angle
    from vidgen.script import writer
    from vidgen.script.llm import LLMChain

    class Record:
        name = "rec"
        prompts = []

        def generate_json(self, prompt, schema):
            self.prompts.append(prompt)
            if schema is writer.Outline:
                return json.dumps({"title": "T", "hook": "H.", "hook_visual_query": "q",
                                   "chapters": [{"title": "A", "summary": "a"}, {"title": "B", "summary": "b"},
                                                {"title": "C", "summary": "c"}]})
            return json.dumps({"scenes": [{"narration": " ".join(["w"] * 20) + ".", "visual_query": "q"}]})

    angle = Angle(style="myth", title="T", hook="Sự thật gây sốc.", key_points=["k1", "k2"], sources=[])
    preset = get_settings().preset("long").model_copy(update={"target_seconds": (10, 12)})
    writer.generate("x", "long", "en", preset, LLMChain([Record()]), [], angle)
    outline, *chapters = Record.prompts
    assert "Opening hook" in outline
    assert chapters and all("Opening hook" not in p and "k1" in p for p in chapters)


def test_non_english_visual_query_is_replaced():
    from vidgen.script import writer
    sc = Scene(id=0, narration="Hi.", visual_query="biển xanh sóng", alt_queries=["ocean waves aerial"])
    [out] = writer.postprocess([sc], 1)
    assert out.visual_query == "ocean waves aerial" and out.alt_queries == []


def test_middle_chapter_can_grow_after_its_last_scene():
    import json

    from vidgen.script import writer
    from vidgen.script.llm import LLMChain

    class Add:
        name = "add"
        prompts = []

        def generate_json(self, prompt, schema):
            self.prompts.append(prompt)
            return json.dumps({"new_scenes": [{"after": 2, "narration": "Mới thêm vào cuối chương.", "visual_query": "q"}]})

    drafts = [writer.LLMScene(narration=t, visual_query="q") for t in ("Một.", "Hai.")]
    out = writer.expand_scenes(drafts, 50, {"facts": "", "angle": ""}, "vi", LLMChain([Add()]), pin_last=False)
    assert [s.narration for s in out][-1] == "Mới thêm vào cuối chương."
    assert "closing question" not in Add.prompts[0]


# --- clip choice ---------------------------------------------------------------------------------------------
def test_judged_clips_are_not_sent_again(tmp_path, monkeypatch):
    from test_vision import FakeStock, FakeVision, tc

    from vidgen.visuals import selector as sel

    def dl(url, cache_dir, http):
        p = tmp_path / "dl" / url.rsplit("/", 1)[-1]
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"x")
        return p
    monkeypatch.setattr(sel, "download", dl)
    monkeypatch.setattr(sel.Selector, "_thumb", lambda self, url: url.encode())
    weak = [tc(f"w{i}", "old ship wreck underwater") for i in range(5)]
    stock = FakeStock({"old ship wreck underwater": weak, "old ship": weak + [tc("good", "old ship")]})
    judge = FakeVision({**{f"w{i}": 4 for i in range(5)}, "good": 9})
    s = sel.Selector([stock], None, get_settings().preset("short"), tmp_path, tmp_path / "c", vision=judge)
    asset = s.pick(Scene(id=1, narration="n", visual_query="old ship wreck underwater"), 5)
    assert asset.uid == "good" and judge.calls == [5, 1]           # only the unseen clip goes to round 2


def test_extra_shot_only_from_clips_the_judge_passed(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    from test_vision import FakeStock, FakeVision, tc

    from vidgen.visuals import selector as sel

    def dl(url, cache_dir, http):
        p = tmp_path / "dl" / url.rsplit("/", 1)[-1]
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"x")
        return p
    monkeypatch.setattr(sel, "download", dl)
    monkeypatch.setattr(sel.Selector, "_thumb", lambda self, url: url.encode())
    q = "ship wreck"
    clips_ = [tc("j0", q)] + [tc(f"j{i}", q) for i in range(1, 5)] + [tc("blind", q, thumb=False)]
    judge = FakeVision({"j0": 9, **{f"j{i}": 1 for i in range(1, 5)}})
    s = sel.Selector([FakeStock({q: clips_})], None, get_settings().preset("short"), tmp_path, tmp_path / "c",
                     vision=judge)
    with ThreadPoolExecutor(2) as pool:
        s.pool = pool
        a = s.pick(Scene(id=1, narration="n", visual_query=q), 8)
        [a] = s.finish([a])
    assert a.uid == "j0" and a.extra == [] and a.extra_uids == []


def test_swap_searches_the_scene_queries_and_avoids_extra_clips(tmp_path, monkeypatch):
    from test_vision import FakeStock, tc

    from vidgen.models import Asset
    from vidgen.visuals import selector as sel

    def dl(url, cache_dir, http):
        p = tmp_path / "dl" / url.rsplit("/", 1)[-1]
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"x")
        return p
    monkeypatch.setattr(sel, "download", dl)
    seen = []

    class Recording(FakeStock):
        def videos(self, q, o):
            seen.append(q)
            return super().videos(q, o)

    stock = Recording({"hacker at laptop": [tc("x3", "hacker at laptop"), tc("new", "hacker laptop typing")]})
    scene = Scene(id=1, narration="n", visual_query="hooded person typing", alt_queries=["hacker at laptop"])
    current = Asset(scene_id=1, path="visuals/scene_001.mp4", kind="video", source="pexels", uid="old",
                    query="hooded")
    other = Asset(scene_id=2, path="v", kind="video", source="pexels", uid="o2", extra=["visuals/s2b.mp4"],
                  extra_uids=["x3"])
    s = sel.Selector([stock], None, get_settings().preset("short"), tmp_path, tmp_path / "c")
    new = s.swap(scene, 5, current, sel.used_ids([current, other]))
    assert "hacker at laptop" in seen and new.uid == "new"


@needs_ffmpeg
def test_user_sounds_named_synth_are_never_deleted(tmp_path):
    from vidgen.assemble import sfx
    lib = sfx.ensure_library(tmp_path)
    mine = tmp_path / sfx.FOLDERS["whoosh"] / "synthwave_hit.wav"
    shutil.copy(lib["whoosh"][0], mine)
    old = tmp_path / sfx.FOLDERS["whoosh"] / "synth_whoosh_1.wav"      # a first-version generated file
    shutil.copy(lib["whoosh"][0], old)
    assert sfx.ensure_library(tmp_path)["whoosh"] == [mine]
    assert mine.exists() and not old.exists()
