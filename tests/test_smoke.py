"""End-to-end render with tiny synthetic media: segments → subtitles → SFX → final mux. Catches wiring
breaks (input indices, filter graphs, file names) that unit tests of each piece can't."""

import json
import shutil
import subprocess

import pytest

from vidgen import ffmpeg
from vidgen.config import get_settings
from vidgen.models import Asset, Scene, SceneAudio, Script, Timeline, WordTiming

pytestmark = [pytest.mark.slow, pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")]


def streams(path) -> set[str]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True).stdout
    return set(out.split())


def test_render_video_end_to_end(tmp_path, monkeypatch):
    import cv2
    import numpy as np

    from vidgen.assemble import render

    monkeypatch.setattr(render, "MUSIC_DIR", tmp_path / "no-music")
    monkeypatch.setattr(render, "SFX_DIR", tmp_path / "sfx")
    out = tmp_path / "video"
    (out / "visuals").mkdir(parents=True)
    img = np.full((1920, 1080, 3), (40, 90, 160), np.uint8)
    cv2.rectangle(img, (300, 600), (800, 1300), (255, 255, 255), 12)
    cv2.imwrite(str(out / "visuals" / "scene_001.jpg"), img)
    ffmpeg.run(["-f", "lavfi", "-i", "sine=f=220:d=3", "-ar", "24000", "-ac", "1", str(out / "voice.wav")])

    script = Script(title="Bí mật", hook="h", lang="vi", format="short", scenes=[
        Scene(id=1, narration="Bí mật được hé lộ năm 2013.", visual_query="q"),
        Scene(id=2, narration="Bạn nghĩ sao?", visual_query="q")])
    w = lambda t, a, b: WordTiming(word=t, start=a, end=b)  # noqa: E731
    tl = Timeline(scenes=[
        SceneAudio(scene_id=1, path="", start=0, duration=1.8, words=[
            w("Bí", .1, .3), w("mật", .3, .5), w("được", .5, .7), w("hé", .7, .9), w("lộ", .9, 1.1),
            w("năm 2013", 1.1, 1.6)]),   # edge-tts merges these two tokens: keep "2013." for the caption
        SceneAudio(scene_id=2, path="", start=1.8, duration=1.2, words=[
            w("Bạn", 1.9, 2.1), w("nghĩ", 2.1, 2.4), w("sao", 2.4, 2.8)])])
    assets = [Asset(scene_id=1, path="visuals/scene_001.jpg", kind="image", source="flux"),
              Asset(scene_id=2, path="", kind="color", source="placeholder")]

    final = render.render_video(script, tl, assets, get_settings().preset("short"), out, "seed", sfx_density="subtle")

    assert final.exists() and not (out / render.PART_FILE).exists()
    assert ffmpeg.duration(final) == pytest.approx(3.0, abs=0.1)
    assert streams(final) == {"video", "audio"}
    cues = json.loads((out / render.SFX_FILE).read_text())
    assert {c["kind"] for c in cues} >= {"impact", "pop"}          # the hook, and "2013" (>0.6 s apart)
    assert json.loads((out / render.MUSIC_FILE).read_text(encoding="utf-8"))["track"] == ""
    ass = (out / "subs.ass").read_text(encoding="utf-8")
    assert "2013." in ass                                            # punctuation kept through the merge
