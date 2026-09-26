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


def ollama(reply: dict | None = None, status=200, seen: list | None = None,
           error="model 'qwen2.5vl:7b' not found"):
    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": error})
        return httpx.Response(200, json={"message": {"content": json.dumps(reply)}})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_judge_sends_one_numbered_strip_and_parses_scores():
    seen = []
    judge = vision.VisionJudge("http://ollama", "qwen2.5vl:7b", http=ollama({"scores": [8, 2]}, seen=seen))
    assert judge.score("Hacker gửi email giả.", ["fake email on laptop"], [jpeg(), jpeg(720, 1280)]) == [8, 2]
    body = json.loads(seen[0].content)
    assert body["model"] == "qwen2.5vl:7b" and body["stream"] is False and "format" in body
    msg = body["messages"][0]
    # one image: a strip of numbered tiles (~1.2k tokens for 5; separate images cost ~1.1k each)
    assert len(msg["images"]) == 1 and "Hacker gửi email giả." in msg["content"] and "2 numbered" in msg["content"]
    strip = cv2.imdecode(np.frombuffer(base64.b64decode(msg["images"][0]), np.uint8), cv2.IMREAD_COLOR)
    assert strip.shape[:2] == (vision.TILE, 2 * vision.TILE)


@pytest.mark.parametrize("reply", [{"scores": [8]}, {"scores": [11, 3]}, {"wrong": 1}])  # too few / out of range
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
    assert json.loads(seen[0].content) == {"model": "qwen3:8b", "keep_alive": 0}


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
    assert len(judge.calls) <= 2 * sel.VISION_CALLS_PER_KIND
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


# --- review findings -------------------------------------------------------------------------------
def test_rejected_clips_never_come_back_and_a_good_image_wins(tmp_path, fake_io):
    q = "ship wreck"
    stock = FakeStock({q: [tc("r1", q), tc("r2", q), tc("r3", q), tc("p1", q, kind="image")]})
    asset = selector(tmp_path, stock, FakeVision({"r1": 1, "r2": 2, "r3": 0, "p1": 9})).pick(
        Scene(id=1, narration="n", visual_query=q), 5)
    assert asset.uid == "p1" and asset.vision_score == 9


def test_weak_video_waits_for_the_image_step(tmp_path, fake_io):
    q = "ship wreck"
    stock = FakeStock({q: [tc("v", q), tc("p", q, kind="image")]})
    asset = selector(tmp_path, stock, FakeVision({"v": 4, "p": 8})).pick(Scene(id=1, narration="n", visual_query=q), 5)
    assert asset.uid == "p"


def test_weak_clip_used_when_nothing_better(tmp_path, fake_io):
    q = "ship wreck"
    asset = selector(tmp_path, FakeStock({q: [tc("v", q)]}), FakeVision({"v": 4})).pick(
        Scene(id=1, narration="n", visual_query=q), 5)
    assert asset.uid == "v" and asset.vision_score == 4


def test_hopeless_clip_never_used_even_as_last_resort(tmp_path, fake_io):
    q = "ship wreck"
    asset = selector(tmp_path, FakeStock({q: [tc("v", q)]}), FakeVision({"v": 0})).pick(
        Scene(id=1, narration="n", visual_query=q), 5)
    assert asset.kind == "color"


def test_bad_thumbnail_is_not_cached_or_sent(tmp_path):
    def handler(request):
        if request.url.path.endswith("bad.jpg"):
            return httpx.Response(200, text="<html>not found</html>")
        return httpx.Response(200, content=jpeg(64, 64))
    s = sel.Selector([], None, get_settings().preset("short"), tmp_path, tmp_path / "cache",
                     http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert s._thumb("https://img/bad.jpg") is None and not (tmp_path / "cache" / "thumbs").exists()
    assert s._thumb("https://img/good.jpg") is not None


def test_first_comma_idea_is_searched_on_its_own(tmp_path, fake_io):
    stock = FakeStock({"ship wreck": [tc("w", "ship wreck")]})
    asset = selector(tmp_path, stock, None).pick(Scene(id=1, narration="n", visual_query="ship wreck, coral reef"), 5)
    assert asset.uid == "w"


def test_judge_gives_up_after_repeated_failures():
    seen = []
    judge = vision.VisionJudge("http://ollama", "m", http=ollama(status=500, seen=seen, error="CUDA out of memory"))
    for _ in range(4):
        assert judge.score("n", ["q"], [jpeg()]) is None
    assert len(seen) == vision.MAX_FAILURES and judge.disabled


def test_script_model_unloaded_once_before_first_judgement():
    seen = []
    judge = vision.VisionJudge("http://ollama", "vl", http=ollama({"scores": [7]}, seen=seen), unload_first="qwen3:8b")
    assert not judge.used
    judge.score("n", ["q"], [jpeg()])
    judge.score("n", ["q"], [jpeg()])
    paths = [r.url.path for r in seen]
    assert paths == ["/api/generate", "/api/ps", "/api/chat", "/api/chat"] and judge.used


def test_vision_session_unloads_only_if_used(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(vision, "unload", lambda url, model, http=None: calls.append(model))
    s = get_settings()
    sel_ = sel.Selector([], None, s.preset("short"), tmp_path, tmp_path,
                        vision=vision.VisionJudge("http://x", "vl"))
    with sel.vision_session(sel_, s):
        pass
    assert calls == []
    sel_.vision.used = True
    with sel.vision_session(sel_, s):
        pass
    assert calls == [s.pipeline.vision.model]


def test_text_judge_kept_as_backup_when_vision_enabled(monkeypatch, tmp_path):
    s = get_settings()
    monkeypatch.setattr(sel, "llm_judge", lambda st: "text-judge")
    monkeypatch.setattr(sel, "stock_clients", lambda st: [])
    from vidgen.models import Script
    script = Script(title="t", hook="h", lang="vi", format="short", scenes=[Scene(id=1, narration="n", visual_query="q")])
    monkeypatch.setattr("vidgen.visuals.comfy.ComfyClient.available", lambda self: False)
    selector_ = sel.build_selector(script, s.preset("short"), tmp_path, s)
    assert selector_.judge == "text-judge" and selector_.vision is not None


def test_unload_waits_until_the_model_left_vram(monkeypatch):
    # Ollama answers keep_alive:0 at once but frees VRAM later; loading the next model before that
    # left it half on the CPU (seen live: 120 s timeouts)
    ps = iter([{"models": [{"name": "qwen3:8b"}]}, {"models": [{"name": "qwen3:8b"}]}, {"models": []}])
    seen = []

    def handler(request):
        seen.append(request.url.path)
        return httpx.Response(200, json=next(ps) if request.url.path == "/api/ps" else {})
    monkeypatch.setattr(vision.time, "sleep", lambda s: None)
    vision.unload("http://ollama", "qwen3:8b", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen == ["/api/generate", "/api/ps", "/api/ps", "/api/ps"]


def test_extra_scores_for_one_image_are_trimmed():
    judge = vision.VisionJudge("http://ollama", "m", http=ollama({"scores": [7, 0, 0]}))
    assert judge.score("n", ["q"], [jpeg()]) == [7]
