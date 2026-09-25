import json

import httpx
import pytest

from vidgen.config import get_settings
from vidgen.models import Scene
from vidgen.visuals import selector as sel
from vidgen.visuals.comfy import ComfyClient, ComfyError, fill_template
from vidgen.visuals.stock import Candidate, Pexels, Pixabay, pick_file

PEXELS_VIDEOS = {"videos": [{
    "id": 1, "width": 1080, "height": 1920, "duration": 12, "url": "https://pexels.com/v/1",
    "user": {"name": "Ann"},
    "video_files": [
        {"file_type": "video/mp4", "width": 2160, "height": 3840, "link": "https://x/4k.mp4"},
        {"file_type": "video/mp4", "width": 1080, "height": 1920, "link": "https://x/hd.mp4"},
        {"file_type": "video/mp4", "width": 540, "height": 960, "link": "https://x/sd.mp4"},
    ]}]}
PIXABAY_VIDEOS = {"hits": [{
    "id": 7, "pageURL": "https://pixabay.com/v/7", "duration": 8, "user": "bob",
    "videos": {"large": {"url": "", "width": 0, "height": 0},
               "medium": {"url": "https://x/m.mp4", "width": 1280, "height": 720}}}]}


def transport(routes: dict[str, dict], seen: list | None = None):
    def handler(request: httpx.Request):
        if seen is not None:
            seen.append(request)
        for prefix, body in routes.items():
            if str(request.url).startswith(prefix):
                return httpx.Response(200, json=body)
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_pick_file_prefers_largest_within_1080p():
    assert pick_file(PEXELS_VIDEOS["videos"][0]["video_files"])["link"] == "https://x/hd.mp4"


def test_pexels_videos_parse_and_cache(tmp_path):
    seen = []
    client = Pexels("k", tmp_path, transport({"https://api.pexels.com/videos": PEXELS_VIDEOS}, seen))
    [c] = client.videos("ocean", "portrait")
    assert (c.uid, c.download_url, c.author, c.duration) == ("pexels:v1", "https://x/hd.mp4", "Ann", 12)
    assert seen[0].headers["Authorization"] == "k"
    client.videos("ocean", "portrait")
    assert len(seen) == 1  # second search served from cache/search/


def test_pixabay_skips_empty_variants(tmp_path):
    client = Pixabay("k", tmp_path, transport({"https://pixabay.com/api/videos": PIXABAY_VIDEOS}))
    [c] = client.videos("ocean", "portrait")
    assert c.download_url == "https://x/m.mp4" and c.width == 1280


def cand(uid, w=1080, h=1920, dur=10.0, kind="video"):
    return Candidate(uid, kind, f"https://x/{uid}.mp4", f"https://page/{uid}", w, h, dur, "a", "pexels", "L")


def test_score_prefers_orientation_resolution_and_length():
    assert sel.score(cand("a"), "portrait", 5) == 7
    assert sel.score(cand("b", 1920, 1080), "portrait", 5) == 4
    assert sel.score(cand("c", dur=2), "portrait", 5) == 5


def test_fallback_queries():
    assert sel.fallback_queries("old ship wreck underwater") == \
        ["old ship wreck underwater", "old ship", "underwater"]
    assert sel.fallback_queries("ocean") == ["ocean"]


class FakeStock:
    source = "pexels"

    def __init__(self, videos=None, photos=None):
        self._v, self._p = videos or {}, photos or {}

    def videos(self, q, o):
        return self._v.get(q, [])

    def photos(self, q, o):
        return self._p.get(q, [])


class FakeAI:
    def __init__(self, fail_video=False):
        self.fail_video = fail_video
        self.calls = []
        self.client = type("C", (), {"free": lambda self: None})()

    def image(self, prompt, out):
        self.calls.append("image")
        p = out.with_suffix(".png"); p.write_bytes(b"png"); return p

    def video(self, prompt, out):
        self.calls.append("video")
        if self.fail_video:
            raise ComfyError("OOM")
        p = out.with_suffix(".mp4"); p.write_bytes(b"mp4"); return p


@pytest.fixture
def fake_download(monkeypatch, tmp_path):
    def dl(url, cache_dir, http):
        p = tmp_path / "dl" / (url.rsplit("/", 1)[-1])
        p.parent.mkdir(exist_ok=True); p.write_bytes(b"x"); return p
    monkeypatch.setattr(sel, "download", dl)


def make_selector(tmp_path, clients, ai=None, fmt="short"):
    return sel.Selector(clients, ai, get_settings().preset(fmt), tmp_path, tmp_path / "cache")


def scene(i, vtype="stock", q="stormy ocean aerial"):
    return Scene(id=i, narration="n", visual_query=q, visual_type=vtype)


def test_stock_video_then_no_reuse(tmp_path, fake_download):
    stock = FakeStock(videos={"stormy ocean aerial": [cand("a"), cand("b", 1920, 1080)]})
    s = make_selector(tmp_path, [stock])
    a1, a2 = s.pick(scene(1), 5), s.pick(scene(2), 5)
    assert a1.url == "https://page/a" and a1.path == "visuals/scene_001.mp4"
    assert a2.url == "https://page/b"  # best clip already used → next best
    assert (tmp_path / "visuals" / "scene_002.mp4").exists()


def test_shorter_query_then_photo_then_placeholder(tmp_path, fake_download):
    stock = FakeStock(videos={"aerial": [cand("v")]},
                      photos={"stormy ocean aerial": [cand("p", kind="image")]})
    s = make_selector(tmp_path, [stock])
    assert s.pick(scene(1), 5).url == "https://page/v"          # found via last-word fallback
    assert s.pick(scene(2), 5).kind == "image"                   # videos exhausted → photo
    assert s.pick(scene(3), 5).kind == "color"                   # nothing left, no AI


def test_ai_video_budget_and_fallback_to_image(tmp_path, fake_download):
    ai = FakeAI()
    s = make_selector(tmp_path, [FakeStock()], ai)  # short preset: max_ai_video=1
    assert s.pick(scene(1, "ai_video"), 5).source == "wan"
    assert s.pick(scene(2, "ai_video"), 5).source == "flux"      # budget spent → still image
    failing = make_selector(tmp_path, [FakeStock()], FakeAI(fail_video=True))
    assert failing.pick(scene(3, "ai_video"), 5).source == "flux"
    assert failing.ai_video_budget == 1                          # failure doesn't consume budget


def test_stock_scene_uses_ai_image_only_as_last_resort(tmp_path, fake_download):
    ai = FakeAI()
    s = make_selector(tmp_path, [FakeStock(videos={"stormy ocean aerial": [cand("a")]})], ai)
    assert s.pick(scene(1), 5).source == "pexels" and ai.calls == []
    assert s.pick(scene(2), 5).source == "flux"


def test_fill_template_keeps_types():
    wf = {"a": {"inputs": {"w": "{{WIDTH}}", "t": "a {{PROMPT}}!", "n": ["1", 0]}}}
    assert fill_template(wf, {"WIDTH": 768, "PROMPT": "sea"}) == \
        {"a": {"inputs": {"w": 768, "t": "a sea!", "n": ["1", 0]}}}


def test_comfy_run_downloads_output(tmp_path):
    wf = tmp_path / "wf.json"
    wf.write_text(json.dumps({"1": {"inputs": {"text": "{{PROMPT}}"}}}))
    posted = []

    def handler(req: httpx.Request):
        if req.url.path == "/prompt":
            posted.append(json.loads(req.content))
            return httpx.Response(200, json={"prompt_id": "p1"})
        if req.url.path == "/history/p1":
            return httpx.Response(200, json={"p1": {"status": {"completed": True, "status_str": "success"},
                                                    "outputs": {"9": {"images": [{"filename": "img_001.png",
                                                                      "subfolder": "vidgen", "type": "output"}]}}}})
        if req.url.path == "/view":
            return httpx.Response(200, content=b"PNG")
        return httpx.Response(404)

    client = ComfyClient("http://c", httpx.Client(transport=httpx.MockTransport(handler)))
    out = client.run(wf, {"PROMPT": "sea"}, tmp_path / "scene_001", timeout=5)
    assert out.name == "scene_001.png" and out.read_bytes() == b"PNG"
    assert posted[0]["prompt"]["1"]["inputs"]["text"] == "sea"


def test_comfy_run_reports_execution_error(tmp_path):
    wf = tmp_path / "wf.json"
    wf.write_text("{}")

    def handler(req):
        if req.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p1"})
        return httpx.Response(200, json={"p1": {"status": {"status_str": "error", "messages": ["OOM"]}}})

    client = ComfyClient("http://c", httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(ComfyError, match="OOM"):
        client.run(wf, {}, tmp_path / "x", timeout=5)
