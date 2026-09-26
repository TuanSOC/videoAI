import base64
import dataclasses
import json

import cv2
import httpx
import numpy as np
import pytest

from vidgen.config import get_settings
from vidgen.models import Scene
from vidgen.visuals import selector as sel
from vidgen.visuals import vision
from vidgen.visuals.stock import Candidate


def jpeg(w=1280, h=720, color=(40, 80, 120)) -> bytes:
    img = np.full((h, w, 3), color, np.uint8)
    return cv2.imencode(".jpg", img)[1].tobytes()


def ollama(reply: dict | None = None, status=200, seen: list | None = None):
    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "model 'qwen2.5vl:7b' not found"})
        return httpx.Response(200, json={"message": {"content": json.dumps(reply)}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_judge_sends_small_images_and_parses_scores():
    seen = []
    judge = vision.VisionJudge("http://ollama", "qwen2.5vl:7b", http=ollama({"scores": [8, 2]}, seen=seen))
    assert judge.score("Hacker gửi email giả.", ["fake email on laptop"], [jpeg(), jpeg()]) == [8, 2]
    body = json.loads(seen[0].content)
    assert body["model"] == "qwen2.5vl:7b" and body["stream"] is False and "format" in body
    msg = body["messages"][0]
    assert len(msg["images"]) == 2 and "Hacker gửi email giả." in msg["content"]
    small = cv2.imdecode(np.frombuffer(base64.b64decode(msg["images"][0]), np.uint8), cv2.IMREAD_COLOR)
    assert max(small.shape[:2]) <= vision.IMAGE_SIDE                     # thumbnails shrunk before sending


@pytest.mark.parametrize("reply", [{"scores": [8]}, {"scores": [11, 3]}, {"wrong": 1}])
def test_judge_rejects_bad_answers(reply):
    judge = vision.VisionJudge("http://ollama", "m", http=ollama(reply))
    assert judge.score("n", ["q"], [jpeg(), jpeg()]) is None


def test_missing_model_disables_judge_once():
    seen = []
    judge = vision.VisionJudge("http://ollama", "m", http=ollama(status=404, seen=seen))
    assert judge.score("n", ["q"], [jpeg()]) is None
    assert judge.score("n", ["q"], [jpeg()]) is None and len(seen) == 1   # no retry storm


def test_unload_posts_keep_alive_zero():
    seen = []
    vision.unload("http://ollama", "qwen3:8b", http=ollama({}, seen=seen))
    body = json.loads(seen[0].content)
    assert body == {"model": "qwen3:8b", "keep_alive": 0}


# --- selector integration -------------------------------------------------------------------------
def tc(uid, text, kind="video", thumb=True):
    return Candidate(uid, kind, f"https://x/{uid}.mp4", f"https://page/{uid}", 1080, 1920, 10.0, "a", "pexels",
                     "L", text, f"https://img/{uid}.jpg" if thumb else "")


class FakeStock:
    source = "pexels"

    def __init__(self, hits):
        self._h = hits

    def videos(self, q, o):
        return [c for c in self._h.get(q, []) if c.kind == "video"]

    def photos(self, q, o):
        return [c for c in self._h.get(q, []) if c.kind == "image"]


class FakeVision:
    """Scores by clip uid; the fake thumbnail bytes are the thumbnail url (see fake_io)."""
    def __init__(self, by_uid):
        self.by_uid, self.calls = by_uid, []

    def score(self, narration, queries, images):
        self.calls.append(len(images))
        return [self.by_uid[img.decode().rsplit("/", 1)[-1].removesuffix(".jpg")] for img in images]


@pytest.fixture
def fake_io(monkeypatch, tmp_path):
    def dl(url, cache_dir, http):
        p = tmp_path / "dl" / url.rsplit("/", 1)[-1]
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"x")
        return p
    monkeypatch.setattr(sel, "download", dl)
    monkeypatch.setattr(sel.Selector, "_thumb", lambda self, url: url.encode())


def selector(tmp_path, stock, judge):
    return sel.Selector([stock], None, get_settings().preset("short"), tmp_path, tmp_path / "cache", vision=judge)


def test_vision_score_decides_the_pick(tmp_path, fake_io):
    stock = FakeStock({"person clicking suspicious link": [
        tc("palm", "person clicking hand"), tc("tap", "person clicking link on phone"), tc("cash", "suspicious person")]})
    judge = FakeVision({"palm": 2, "tap": 9, "cash": 1})
    asset = selector(tmp_path, stock, judge).pick(
        Scene(id=1, narration="n", visual_query="person clicking suspicious link"), 5)
    assert asset.uid == "tap" and asset.vision_score == 9
    assert asset.alternates == []                                  # low scorers are not offered as swaps


def test_image_scored_one_point_lower_than_video(tmp_path, fake_io):
    stock = FakeStock({"ocean waves": [tc("img", "ocean waves", kind="image"), tc("vid", "ocean waves")]})
    asset = selector(tmp_path, stock, FakeVision({"img": 8, "vid": 8})).pick(
        Scene(id=1, narration="n", visual_query="ocean waves"), 5)
    assert asset.uid == "vid"


def test_all_low_scores_keep_best_rather_than_placeholder(tmp_path, fake_io):
    stock = FakeStock({"server room lights": [tc("a", "server room"), tc("b", "server room lights")]})
    asset = selector(tmp_path, stock, FakeVision({"a": 3, "b": 4})).pick(
        Scene(id=1, narration="n", visual_query="server room lights"), 5)
    assert asset.uid == "b" and asset.vision_score == 4


def test_no_vision_opinion_keeps_text_order(tmp_path, fake_io):
    class NoOpinion:
        def score(self, *a):
            return None
    stock = FakeStock({"ocean waves": [tc("a", "ocean waves crashing"), tc("b", "calm sea")]})
    asset = selector(tmp_path, stock, NoOpinion()).pick(Scene(id=1, narration="n", visual_query="ocean waves"), 5)
    assert asset.uid == "a" and asset.vision_score is None


def test_candidates_without_thumbnails_are_not_sent(tmp_path, fake_io):
    stock = FakeStock({"ocean waves": [tc("a", "ocean waves", thumb=False), tc("b", "ocean waves big")]})
    judge = FakeVision({"b": 8})
    selector(tmp_path, stock, judge).pick(Scene(id=1, narration="n", visual_query="ocean waves"), 5)
    assert judge.calls == [1]


def test_vision_calls_capped_per_scene(tmp_path, fake_io):
    # every round only finds poor clips: the selector keeps looking, but not with unlimited model calls
    rounds = {"old ship wreck underwater": [tc("r1", "old ship wreck underwater")],
              "old ship": [tc("r2", "old ship")], "wreck underwater": [tc("r3", "wreck underwater")]}
    judge = FakeVision({"r1": 2, "r2": 3, "r3": 1})
    asset = selector(tmp_path, FakeStock(rounds), judge).pick(
        Scene(id=1, narration="n", visual_query="old ship wreck underwater"), 5)
    assert len(judge.calls) <= sel.VISION_CALLS_PER_SCENE
    assert asset.uid in {"r1", "r2", "r3"}


def test_adjective_is_never_the_video_subject():
    qs = ["fake login page", "fake bank email", "fake website", "phone screen", "fake qr code"]
    assert sel.video_subject(qs) is None


def test_after_vision_budget_unjudged_clips_are_not_taken_blindly(tmp_path, fake_io):
    # the first rounds only find poor clips (judged); later rounds must not pick unjudged ones by name
    rounds = {"old ship wreck underwater": [tc("r1", "old ship wreck underwater")],
              "old ship": [tc("r2", "old ship")], "wreck underwater": [tc("r3", "wreck underwater")],
              "ship": [tc("r4", "ship")]}
    judge = FakeVision({"r1": 4, "r2": 3, "r3": 2, "r4": 1})
    asset = selector(tmp_path, FakeStock(rounds), judge).pick(
        Scene(id=1, narration="n", visual_query="old ship wreck underwater"), 5)
    assert asset.vision_score is not None and asset.uid == "r1"


def test_hopeless_scores_fall_through_to_next_step(tmp_path, fake_io):
    stock = FakeStock({"ocean waves": [tc("v", "ocean waves"), tc("p", "ocean waves", kind="image")]})
    # video round: 1/10 → no fallback (below MIN_FALLBACK); then the image step
    judge = FakeVision({"v": 1, "p": 8})
    asset = selector(tmp_path, stock, judge).pick(Scene(id=1, narration="n", visual_query="ocean waves"), 5)
    assert asset.uid == "p"
