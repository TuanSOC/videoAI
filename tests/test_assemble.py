import pytest

from vidgen.assemble import subtitles as subs
from vidgen.assemble.clips import frame_counts, segment_args
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


def test_short_ass_highlights_each_word_once():
    script, tl = make("short")
    ass = subs.build_ass(script, tl, get_settings().preset("short"))
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue")]
    assert len(dialogues) == 8  # one event per spoken word
    assert all(l.count(subs.HIGHLIGHT) == 1 for l in dialogues)
    assert "PlayResY: 1920" in ass


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


def test_segment_args_per_kind(tmp_path):
    p = get_settings().preset("short")
    video = segment_args(Asset(scene_id=1, path="v.mp4", kind="video", source="pexels"), tmp_path, 45, p, tmp_path / "o.mp4")
    assert "-stream_loop" in video and "45" in video
    image = segment_args(Asset(scene_id=1, path="i.jpg", kind="image", source="flux"), tmp_path, 45, p, tmp_path / "o.mp4")
    assert any("zoompan" in a for a in image)
    color = segment_args(Asset(scene_id=1, path="", kind="color", source="placeholder"), tmp_path, 45, p, tmp_path / "o.mp4")
    assert any(a.startswith("color=") for a in color)


@pytest.mark.parametrize("music", [None, "m.mp3"])
def test_final_args(music):
    from pathlib import Path

    args = final_args(12.3456, Path(music) if music else None)
    assert args[-1] == "final.mp4" and "12.346" in args
    graph = args[args.index("-filter_complex") + 1]
    assert ("sidechaincompress" in graph) == bool(music)
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
    assert subs.two_lines(["a", "b", "c", "extraordinarily"]) == r"a b c\Nextraordinarily"


def test_normalize_hashtags():
    from vidgen.metadata import normalize_hashtags

    assert normalize_hashtags(["bermuda", "#Bí ẩn", "#bermuda", "#"], short=True) == \
        ["#bermuda", "#Bíẩn", "#shorts"]


def test_credit_lines_dedupe_and_skip_ai():
    assets = [Asset(scene_id=1, path="", kind="video", source="pexels", url="u1", author="Ann"),
              Asset(scene_id=2, path="", kind="video", source="pexels", url="u1", author="Ann"),
              Asset(scene_id=3, path="", kind="image", source="flux")]
    assert credit_lines(assets) == ["Pexels by Ann: u1"]
