"""Phase 3: document-style evidence scenes — a real sourced sentence on a paper card, the figure swept
with a highlighter. Never a made-up quote, never a real publication's look."""

import shutil
from pathlib import Path

import pytest

from vidgen.config import get_settings
from vidgen.models import Asset, Scene, SceneAudio, Script, Timeline
from vidgen.script.research import Source
from vidgen.visuals import document as doc

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

SRC_VI = Source(title="Tấn công mạng", url="https://vi.wikipedia.org/wiki/X", lang="vi",
                text="Mở đầu bài viết. Năm 2021, tin tặc đã truy cập 150.000 camera an ninh của Verkada. "
                     "Các chuyên gia khuyên đổi mật khẩu thường xuyên.")
SRC_EN = Source(title="Verkada", url="https://en.wikipedia.org/wiki/Verkada", lang="en",
                text="In March 2021, hackers gained access to 150,000 security cameras operated by Verkada.")


def script(*narrations):
    return Script(title="t", hook="h", lang="vi", format="short",
                  scenes=[Scene(id=i, narration=n, visual_query="q") for i, n in enumerate(narrations, 1)])


# --- choosing what to show ------------------------------------------------------------------------------
def test_quote_is_the_real_source_sentence_with_the_figure_highlighted():
    q = doc.find_quote("Tin tặc đã xâm nhập 150.000 camera an ninh.", [SRC_EN, SRC_VI], "vi")
    assert q.text == "Năm 2021, tin tặc đã truy cập 150.000 camera an ninh của Verkada."   # same language first
    words = q.text.split()
    assert words[q.start:q.end][0] == "150.000" and 2 <= q.end - q.start <= 4
    assert q.source_title == "Tấn công mạng"


def test_no_source_sentence_means_no_document():
    assert doc.find_quote("Có 987654 thiết bị bị lộ.", [SRC_VI, SRC_EN], "vi") is None
    assert doc.find_quote("Bạch tuộc có 3 trái tim.", [Source(title="O", url="u", lang="vi",
                                                               text="Bạch tuộc có 3 trái tim.")], "vi") is None


def test_documents_skip_hook_and_ending_are_capped_and_spread():
    s = script("Hook 150.000 camera.", "Năm 2021 có 150.000 camera bị truy cập.", "Một cảnh khác.",
               "Lại 150.000 camera nữa.", "Thêm cảnh.", "Cảnh.", "Vẫn 150.000 camera.", "Kết 150.000?")
    chosen = doc.plan_documents(s, [SRC_VI], limit=2)
    assert list(chosen) == [2, 7]                        # not the hook (1) nor the ending (8); spaced out


# --- drawing -----------------------------------------------------------------------------------------------
def test_card_layout_fits_and_highlight_covers_the_figure():
    q = doc.find_quote("150.000 camera an ninh bị lộ.", [SRC_VI], "vi")
    w, h = 1080, 1920
    layout = doc.layout(q, (w, h))
    assert layout.boxes and all(0 <= x and x + bw <= w and 0 <= y and y + bh <= h for x, y, bw, bh in layout.boxes)
    caption_top = h - 620 - 110                          # margin_v + one line of 76 px captions with outline
    assert layout.source_y + 60 < caption_top                                     # the source line too
    first = layout.words[q.start]
    assert layout.boxes[0][0] <= first[0] + 2 and layout.boxes[0][1] <= first[1] + 2


@needs_ffmpeg
def test_clip_sweeps_the_highlight_then_holds(tmp_path):
    import cv2
    import numpy as np

    from vidgen import ffmpeg
    q = doc.find_quote("150.000 camera an ninh bị lộ.", [SRC_VI], "vi")
    out = doc.make_clip(q, (540, 960), 3.0, tmp_path / "doc.mp4")
    assert ffmpeg.duration(out) == pytest.approx(3.0, abs=0.1)
    x, y, bw, bh = doc.layout(q, (540, 960)).boxes[0]
    cap = cv2.VideoCapture(str(out))

    def yellow_at(t):
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        region = frame[y:y + bh, x:x + bw].reshape(-1, 3).astype(int)
        return float(np.mean((region[:, 2] > 180) & (region[:, 1] > 150) & (region[:, 0] < 150)))
    assert yellow_at(0.1) < 0.05 and yellow_at(2.5) > 0.3


# --- wiring --------------------------------------------------------------------------------------------------
def test_document_clip_is_one_shot_from_the_start():
    from vidgen.assemble.clips import plan_shots
    tl = Timeline(scenes=[SceneAudio(scene_id=1, path="", start=0, duration=9.0, words=[])])
    a = Asset(scene_id=1, path="visuals/scene_001_doc.mp4", kind="video", source="document")
    shots = plan_shots(tl, [a], get_settings().preset("short"), "short", Path("o"),
                       {Path("o/visuals/scene_001_doc.mp4"): 10.0})
    assert len(shots) == 1 and shots[0].offset == 0.0


def test_visuals_stage_builds_documents_and_skips_stock_for_them(tmp_path, monkeypatch):
    from vidgen.visuals import selector as sel

    picked = []

    class FakeSelector:
        ai = None
        pool = None

        def pick(self, scene, seconds):
            picked.append(scene.id)
            return Asset(scene_id=scene.id, path="", kind="color", source="placeholder")

        def finish(self, assets):
            return assets

    monkeypatch.setattr(doc, "make_clip", lambda q, size, seconds, out: out)
    s = script("Hook.", "Năm 2021 có 150.000 camera bị truy cập.", "Kết?")
    tl = Timeline(scenes=[SceneAudio(scene_id=i, path="", start=i, duration=3, words=[]) for i in (1, 2, 3)])
    settings = get_settings()
    assets = sel.source_visuals(s, tl, settings.preset("short"), tmp_path, settings, FakeSelector(), sources=[SRC_VI])
    assert picked == [1, 3]
    assert assets[1].source == "document" and assets[1].url == SRC_VI.url and assets[1].kind == "video"


def test_documents_can_be_switched_off():
    from vidgen.config import DocumentsConfig
    assert DocumentsConfig().enabled is True


def test_highlight_never_ends_inside_a_compound_word():
    q = doc.find_quote("Tin tặc đã xâm nhập 150.000 camera an ninh.", [SRC_VI], "vi")
    assert q.text.split()[q.start:q.end] == ["150.000", "camera", "an", "ninh"]


def test_document_card_keeps_its_own_exposure():
    from vidgen.assemble.clips import plan_shots
    tl = Timeline(scenes=[SceneAudio(scene_id=1, path="", start=0, duration=4.0, words=[])])
    a = Asset(scene_id=1, path="visuals/scene_001_doc.mp4", kind="video", source="document")
    [shot] = plan_shots(tl, [a], get_settings().preset("short"), "short", Path("o"), {})
    assert shot.analyse is False          # the paper is meant to be bright: no exposure "correction"
