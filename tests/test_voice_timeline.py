import asyncio
import shutil

import pytest

from vidgen.models import WordTiming
from vidgen.voice.builder import build_timeline


def w(word, start, end):
    return WordTiming(word=word, start=start, end=end)


def test_build_timeline_offsets_words():
    tl = build_timeline(
        [1, 2], ["voice/a.wav", "voice/b.wav"], [2.0, 3.5],
        [[w("hi", 0.1, 0.4)], [w("there", 0.2, 0.6), w("you", 0.7, 0.9)]],
    )
    assert [s.start for s in tl.scenes] == [0.0, 2.0]
    assert tl.scenes[1].words[0].start == pytest.approx(2.2)
    assert tl.scenes[1].words[1].end == pytest.approx(2.9)
    assert tl.duration == pytest.approx(5.5)


def test_synth_edge_raises_clear_error_when_offline(monkeypatch, tmp_path):
    import asyncio

    from vidgen.voice import tts

    class Down:
        def __init__(self, *a, **k):
            pass

        async def stream(self):
            raise ConnectionError("offline")
            yield  # pragma: no cover

    monkeypatch.setattr(tts.edge_tts, "Communicate", Down)
    real_sleep = asyncio.sleep
    monkeypatch.setattr(tts.asyncio, "sleep", lambda s: real_sleep(0))
    with pytest.raises(tts.TTSError, match="Check internet access"):
        asyncio.run(tts.synth_edge("hi", "v", tmp_path / "a.mp3"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_generate_voice_reuses_finished_scenes_after_failure(tmp_path):
    from vidgen import ffmpeg
    from vidgen.config import get_settings
    from vidgen.models import Scene, Script
    from vidgen.voice.builder import generate_voice
    from vidgen.voice.tts import TTSError

    calls = []
    failed_once = []

    async def flaky(text, voice, out):
        calls.append(text)
        if text == "two" and not failed_once:
            failed_once.append(True)
            raise TTSError("throttled")
        ffmpeg.run(["-f", "lavfi", "-i", "sine=d=0.5", "-c:a", "libmp3lame", str(out)])
        return [w(text, 0.0, 0.3)]

    script = Script(title="t", hook="h", lang="en", format="short", scenes=[
        Scene(id=1, narration="one", visual_query="q"), Scene(id=2, narration="two", visual_query="q")])
    with pytest.raises(TTSError, match="scene 2"):
        generate_voice(script, tmp_path, get_settings(), synth=flaky, aligner=lambda p, l: [])
    calls.clear()
    generate_voice(script, tmp_path, get_settings(), synth=flaky, aligner=lambda p, l: [])
    assert calls == ["two"]  # scene 1 reused from its sidecar


def test_build_timeline_length_mismatch_raises():
    with pytest.raises(ValueError):
        build_timeline([1, 2], ["a"], [1.0], [[]])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_generate_voice_with_fake_tts(tmp_path):
    """Full stage with a fake synth that writes a sine tone; checks PCM padding and concat."""
    from vidgen import ffmpeg
    from vidgen.config import get_settings
    from vidgen.models import Scene, Script
    from vidgen.voice.builder import GAP_SECONDS, generate_voice

    async def fake_synth(text, voice, out):
        ffmpeg.run(["-f", "lavfi", "-i", "sine=f=440:d=1", "-c:a", "libmp3lame", str(out)])
        return [w(text, 0.1, 0.5)]

    script = Script(title="t", hook="h", lang="en", format="short", scenes=[
        Scene(id=1, narration="one", visual_query="q"), Scene(id=2, narration="two", visual_query="q")])
    tl = generate_voice(script, tmp_path, get_settings(), synth=fake_synth, aligner=lambda p, l: [])

    assert (tmp_path / "voice.wav").exists() and (tmp_path / "timeline.json").exists()
    # trimmed to the speech: 0.08s before the word (0.1) … 0.2s after it (0.5), not the whole 1s file
    assert tl.scenes[0].duration == pytest.approx(0.68 + GAP_SECONDS, abs=0.06)
    assert ffmpeg.duration(tmp_path / "voice.wav") == pytest.approx(tl.duration, abs=0.01)
    assert tl.scenes[1].words[0].start == pytest.approx(tl.scenes[1].start + 0.08, abs=0.01)


# --- grouped voicing ------------------------------------------------------------------------------
def _scene(i, text):
    from vidgen.models import Scene
    return Scene(id=i, narration=text, visual_query="q")


def test_plan_groups_packs_consecutive_scenes():
    from vidgen.voice.builder import plan_groups
    scenes = [_scene(i, " ".join(["w"] * n)) for i, n in enumerate([60, 60, 60, 200, 10], 1)]
    assert [[s.id for s in g] for g in plan_groups(scenes, 160)] == [[1, 2], [3], [4], [5]]


def test_split_by_scene_handles_split_tokens_and_rejects_mismatch():
    from vidgen.voice.builder import split_by_scene
    scenes = [_scene(1, "Video AI-generated."), _scene(2, "Hết, rồi.")]
    words = [w("Video", 0, .3), w("AI", .3, .5), w("generated", .5, .9), w("Hết", 1.2, 1.4), w("rồi", 1.4, 1.6)]
    split = split_by_scene(scenes, words)
    assert [[x.word for x in s] for s in split] == [["Video", "AI", "generated"], ["Hết", "rồi"]]
    assert split_by_scene(scenes, words[:-1]) is None                       # missing word
    assert split_by_scene(scenes, [w("Other", 0, 1)] + words[1:]) is None   # different text


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_grouped_voice_one_request_cut_at_pauses(tmp_path):
    from vidgen import ffmpeg
    from vidgen.config import get_settings
    from vidgen.models import Script
    from vidgen.voice.builder import GAP_SECONDS, generate_voice

    calls = []

    async def fake_synth(text, voice, out):
        """Each word 0.4s, then a 0.2s pause (sentence ends add 0.6s)."""
        calls.append(text)
        t, ws = 0.1, []
        for tok in text.split():
            ws.append(w(tok.strip(".,"), t, t + 0.4))
            t += 0.6 + (0.6 if tok.endswith(".") else 0)
        ffmpeg.run(["-f", "lavfi", "-i", f"sine=f=440:d={t:.2f}", "-c:a", "libmp3lame", str(out)])
        return ws

    script = Script(title="t", hook="h", lang="en", format="short", scenes=[
        _scene(1, "one two."), _scene(2, "three four five."), _scene(3, "six.")])
    tl = generate_voice(script, tmp_path, get_settings(), synth=fake_synth, aligner=lambda p, l: [])

    assert calls == ["one two. three four five. six."]  # a single request for all three scenes
    s1, s2, s3 = tl.scenes
    assert [x.word for x in s2.words] == ["three", "four", "five"]
    # tight to the speech: scene 1 runs 0.02 (0.08 before "one") → 1.3 (0.2 after "two"),
    # scene 2 starts 0.08 before "three" — the rest of the 0.8s pause is dropped
    assert s1.duration == pytest.approx(1.28 + GAP_SECONDS, abs=0.06)
    assert s2.words[0].start == pytest.approx(s2.start + 0.08, abs=0.06)
    gap = s2.words[0].start - s1.words[-1].end
    assert gap < 0.4
    assert ffmpeg.duration(tmp_path / "voice.wav") == pytest.approx(tl.duration, abs=0.02)


def test_scene_bounds_keep_little_silence_and_never_cut_a_word():
    from vidgen.voice.builder import scene_bounds
    assert scene_bounds(1.0, 2.2) == pytest.approx((1.2, 2.12))   # long pause: 0.2 after, 0.08 before
    assert scene_bounds(1.0, 1.1) == pytest.approx((1.05, 1.05))  # short pause: cut in the middle
    assert scene_bounds(2.1, 2.0) == pytest.approx((2.1, 2.1))    # overlapping words: after the last one


def test_synth_edge_passes_speaking_rate(monkeypatch, tmp_path):
    from vidgen.voice import tts

    seen = {}

    class FakeCommunicate:
        def __init__(self, text, voice, **kw):
            seen.update(kw)

        async def stream(self):
            yield {"type": "audio", "data": b"mp3"}

    monkeypatch.setattr(tts.edge_tts, "Communicate", FakeCommunicate)
    asyncio.run(tts.synth_edge("hi", "v", tmp_path / "a.mp3", rate="+8%"))
    assert seen["rate"] == "+8%"


def test_voice_rate_setting():
    from vidgen.config import get_settings
    assert get_settings().pipeline.voice_rate.endswith("%")


def test_split_ignores_punctuation_boundaries_and_nfc_differences():
    import unicodedata
    from vidgen.voice.builder import split_by_scene
    nfd = unicodedata.normalize("NFD", "Bạch tuộc.")
    scenes = [_scene(1, nfd)]
    words = [w("Bạch", 0, .3), w("tuộc", .3, .6), w("…", .6, .6)]
    assert [[x.word for x in s] for s in split_by_scene(scenes, words)] == [["Bạch", "tuộc"]]
