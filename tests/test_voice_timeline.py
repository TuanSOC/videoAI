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
    assert tl.scenes[0].duration == pytest.approx(1 + GAP_SECONDS, abs=0.06)
    assert ffmpeg.duration(tmp_path / "voice.wav") == pytest.approx(tl.duration, abs=0.01)
    assert tl.scenes[1].words[0].start == pytest.approx(tl.scenes[1].start + 0.1)
