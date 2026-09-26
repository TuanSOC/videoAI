import json

import httpx
import pytest

from vidgen.config import get_settings
from vidgen.models import Asset, Scene
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


def test_replace_retries_on_windows_lock(tmp_path, monkeypatch):
    import os

    from vidgen import fsutil

    src, dest = tmp_path / "a.part", tmp_path / "a.mp4"
    src.write_bytes(b"x")
    real, calls = os.replace, []

    def flaky(a, b):
        calls.append(1)
        if len(calls) < 3:
            raise PermissionError(32, "being used by another process")
        return real(a, b)

    monkeypatch.setattr(fsutil.os, "replace", flaky)
    monkeypatch.setattr(fsutil.time, "sleep", lambda s: None)
    fsutil.replace_with_retry(src, dest)
    assert dest.read_bytes() == b"x" and len(calls) == 3


def cand(uid, w=1080, h=1920, dur=10.0, kind="video"):
    return Candidate(uid, kind, f"https://x/{uid}.mp4", f"https://page/{uid}", w, h, dur, "a", "pexels", "L")


def test_score_prefers_orientation_resolution_and_length():
    assert sel.score(cand("a"), "portrait", 5) == 7
    assert sel.score(cand("b", 1920, 1080), "portrait", 5) == 4
    assert sel.score(cand("c", dur=2), "portrait", 5) == 5


def test_fallback_queries():
    assert sel.fallback_queries("old ship wreck underwater") == \
        ["old ship wreck underwater", "old ship", "wreck underwater"]
    assert sel.fallback_queries("ocean") == ["ocean"]
    # real Gemini output lists several ideas: each becomes its own query
    assert sel.fallback_queries("ocean map, red triangle outline, compass")[:3] == \
        ["ocean map", "red triangle outline", "compass"]


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
    stock = FakeStock(videos={"ocean aerial": [cand("v")]},
                      photos={"stormy ocean aerial": [cand("p", kind="image")]})
    s = make_selector(tmp_path, [stock])
    assert s.pick(scene(1), 5).url == "https://page/v"          # found via last-two-words fallback
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


# --- clip swap ---------------------------------------------------------------------------------
def test_pick_records_ranked_alternates(tmp_path, fake_download):
    stock = FakeStock(videos={"stormy ocean aerial": [cand("a"), cand("b", dur=2), cand("c", 1920, 1080),
                                                       cand("d", 1920, 1080), cand("e", 1920, 1080)]})
    asset = make_selector(tmp_path, [stock]).pick(scene(1), 5)
    assert asset.uid == "a" and asset.query == "stormy ocean aerial"
    assert [x.uid for x in asset.alternates] == ["b", "c", "d"]  # next best, capped at 3


def test_swap_uses_next_unused_alternate(tmp_path, fake_download):
    stock = FakeStock(videos={"stormy ocean aerial": [cand("a"), cand("b"), cand("c")]})
    s = make_selector(tmp_path, [stock])
    current = s.pick(scene(1), 5)
    new = s.swap(scene(1), 5, current, used={"a", "b"})  # "b" is already used by another scene
    assert new.uid == "c" and new.alternates == []


def test_swap_with_query_searches_and_never_reuses(tmp_path, fake_download):
    stock = FakeStock(videos={"stormy ocean aerial": [cand("a")], "lightning storm": [cand("a"), cand("z")]})
    s = make_selector(tmp_path, [stock])
    current = s.pick(scene(1), 5)
    new = s.swap(scene(1), 5, current, used={"a"}, query="lightning storm")
    assert new.uid == "z" and new.query == "lightning storm"


def test_swap_raises_when_nothing_left(tmp_path, fake_download):
    stock = FakeStock(videos={"stormy ocean aerial": [cand("a")]})
    s = make_selector(tmp_path, [stock])
    current = s.pick(scene(1), 5)
    with pytest.raises(sel.SwapError):
        s.swap(scene(1), 5, current, used={"a"})


def test_metadata_rebuild_refreshes_credits_without_llm():
    from vidgen.metadata import Metadata, rebuild_description
    from vidgen.models import Script

    script = Script(title="t", hook="h", lang="vi", format="short", scenes=[scene(1)])
    old = Metadata(title="T", description="Tóm tắt.\n\nVideo sử dụng giọng đọc AI; một số hình ảnh minh họa được tạo bằng AI."
                   "\n\nFootage:\n- Pexels by A: u1\n\n#x #shorts",
                   tags=["x"], hashtags=["#x", "#shorts"], credits=["Pexels by A: u1"],
                   ai_disclosure="Video sử dụng giọng đọc AI; một số hình ảnh minh họa được tạo bằng AI.",
                   ai_visuals_used=False)  # older file: no `summary`
    new_assets = [Asset(scene_id=1, path="p", kind="video", source="pixabay", url="u9", author="Bo")]
    meta = rebuild_description(old, script, new_assets)
    assert meta.summary == "Tóm tắt." and meta.credits == ["Pixabay by Bo: u9"]
    assert "u1" not in meta.description and meta.description.endswith("#x #shorts")


def test_swap_never_cycles_back_to_rejected_clips(tmp_path, fake_download):
    stock = FakeStock(videos={"stormy ocean aerial": [cand("a"), cand("b")]})
    s = make_selector(tmp_path, [stock])
    first = s.pick(scene(1), 5)                      # a (alternate: b)
    second = s.swap(scene(1), 5, first, used={"a"})  # b
    assert second.uid == "b" and second.rejected == ["a"]
    with pytest.raises(sel.SwapError):               # only a and b exist: a was rejected
        s.swap(scene(1), 5, second, used={"b"})


def test_swap_on_old_assets_excludes_by_page_url(tmp_path, fake_download):
    stock = FakeStock(videos={"stormy ocean aerial": [cand("a"), cand("b")]})
    s = make_selector(tmp_path, [stock])
    old = Asset(scene_id=1, path="visuals/scene_001.mp4", kind="video", source="pexels", url="https://page/a")
    new = s.swap(scene(1), 5, old, used={"https://page/a"})  # pre-uid asset: only the url is known
    assert new.uid == "b"


# --- relevance ------------------------------------------------------------------------------------
def tcand(uid, text, w=1080, h=1920, dur=10.0):
    return Candidate(uid, "video", f"https://x/{uid}.mp4", f"https://page/{uid}", w, h, dur, "a", "pexels", "L", text)


def test_stem_is_idempotent():
    for w in ("octopus", "octopuses", "caves", "cave", "bodies", "glass", "houses", "boxes", "images"):
        assert sel.stem(sel.stem(w)) == sel.stem(w)
    for a, b in [("octopuses", "octopus"), ("caves", "cave"), ("houses", "house"), ("pauses", "pause"),
                 ("boxes", "box"), ("buses", "bus"), ("images", "image"), ("bodies", "body"),
                 ("glasses", "glass"), ("viruses", "virus")]:
        assert sel.stem(a) == sel.stem(b) == b, (a, b)   # the key is the real singular word
    for a, b in [("car", "care"), ("plan", "plane"), ("fir", "fire"), ("hat", "hate")]:
        assert sel.stem(a) != sel.stem(b), (a, b)        # different words must not merge


def test_fallback_queries_use_real_words_even_after_commas_and_punctuation():
    q = "deep sea footage, octopus hiding in caves."
    subject = sel.stem("octopus")
    out = sel.fallback_queries(q, subject)
    assert "octopus" in out and all(t.isalpha() or " " in t or "," in t for t in out)
    assert sel.fallback_queries("octopus in caves.", "octopus")[1:] == ["octopus caves", "octopus"]


def test_video_subject():
    qs = ["octopus shell on rock", "diver looking for octopus", "octopus in cave", "marine ecosystem", "sand"]
    assert sel.video_subject(qs) == "octopus"
    assert sel.video_subject(["computer screen", "server room", "hacker typing", "data center", "laptop"]) is None


def test_relevance_prefers_matching_description_and_requires_subject():
    q = sel.content_words("octopus in cold water")
    assert sel.relevance(tcand("a", "octopus swimming in cold water"), q, "octopus") == 4
    assert sel.relevance(tcand("b", "a person cooking an octopus"), q, "octopus") == 0  # off-context
    assert sel.relevance(tcand("b2", "octopus in aquarium"), q, "octopus") == 2
    dish_q = sel.content_words("grilled octopus dish")
    assert sel.relevance(tcand("b3", "grilled octopus dish"), dish_q, "octopus") == 4  # asked for
    assert sel.relevance(tcand("c", "aerial view of cave entrances"), q, "octopus") == 0
    assert sel.relevance(tcand("d", ""), q, "octopus") == 1  # unknown description: neutral


def test_fallback_queries_keep_subject():
    assert sel.fallback_queries("octopus moving between caves", "octopus") == \
        ["octopus moving between caves", "octopus caves", "octopus"]  # real words, not stems


def test_pick_ranks_relevance_above_technical_fit(tmp_path, fake_download):
    stock = FakeStock(videos={"octopus in cold water": [
        tcand("cooking", "a person cooking an octopus"),                     # perfect framing, weak match
        tcand("swim", "octopus swimming in cold water", 1920, 1080, 2.0)]})  # landscape + short, strong match
    s = make_selector(tmp_path, [stock])
    s.subject = "octopus"
    assert s.pick(scene(1, q="octopus in cold water"), 5).uid == "swim"


def test_strict_skips_irrelevant_then_subject_only_query(tmp_path, fake_download):
    stock = FakeStock(videos={"octopus moving between caves": [tcand("cave", "nemrut dagi cave entrances")],
                              "octopus caves": [],
                              "octopus": [tcand("oct", "octopus crawling on reef")]})
    s = make_selector(tmp_path, [stock])
    s.subject = "octopus"
    assert s.pick(scene(1, q="octopus moving between caves"), 5).uid == "oct"


def test_irrelevant_clip_used_only_as_last_resort(tmp_path, fake_download):
    stock = FakeStock(videos={"octopus": [tcand("cave", "cave entrances")]})
    s = make_selector(tmp_path, [stock])
    s.subject = "octopus"
    assert s.pick(scene(1, q="octopus"), 5).uid == "cave"  # better than a flat colour


def test_judge_called_only_for_weak_matches(tmp_path, fake_download):
    calls = []

    def judge(narration, query, texts):
        calls.append(texts)
        return texts.index("octopus hiding in reef")

    weak = FakeStock(videos={"octopus den": [tcand("tank", "octopus in aquarium"), tcand("reef", "octopus hiding in reef")]})
    s = make_selector(tmp_path, [weak])
    s.subject, s.judge = "octopus", judge
    assert s.pick(scene(1, q="octopus den"), 5).uid == "reef" and len(calls) == 1

    strong = FakeStock(videos={"octopus den": [tcand("den", "octopus in its den"), tcand("x", "octopus")]})
    s2 = make_selector(tmp_path, [strong])
    s2.subject, s2.judge = "octopus", judge
    assert s2.pick(scene(2, q="octopus den"), 5).uid == "den" and len(calls) == 1  # no extra call


def test_judge_rejecting_all_moves_on_to_simpler_query(tmp_path, fake_download):
    stock = FakeStock(videos={"octopus den": [tcand("tank", "octopus in aquarium"), tcand("art", "octopus mural wall")],
                              "octopus": [tcand("live", "octopus crawling on reef")]})
    s = make_selector(tmp_path, [stock])
    s.subject = "octopus"
    s.judge = lambda n, q, texts: -1 if "octopus in aquarium" in texts else None
    assert s.pick(scene(1, q="octopus den"), 5).uid == "live"


def test_parallel_downloads_and_failed_one_falls_back_to_alternate(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    def dl(url, cache_dir, http):
        if url.endswith("/a.mp4"):
            raise httpx.ConnectError("reset")
        p = tmp_path / "dl" / url.rsplit("/", 1)[-1]
        p.parent.mkdir(exist_ok=True)
        p.write_bytes(b"x")
        return p

    monkeypatch.setattr(sel, "download", dl)
    stock = FakeStock(videos={"stormy ocean aerial": [cand("a"), cand("b"), cand("c")],
                              "calm lake": [cand("d")]})
    s = make_selector(tmp_path, [stock])
    with ThreadPoolExecutor(4) as pool:
        s.pool = pool
        first = s.pick(scene(1), 5)                   # "a": download will fail in the background
        second = s.pick(scene(2, q="calm lake"), 5)
        assert first.uid == "a"                       # returned before the download finished
        assets = s.finish([first, second])
    assert [a.uid for a in assets] == ["b", "d"]      # scene 1 repaired with its next alternate
    assert (tmp_path / "visuals" / "scene_001.mp4").exists()


def test_long_scene_gets_extra_clip_short_scene_does_not(tmp_path, fake_download):
    from concurrent.futures import ThreadPoolExecutor

    stock = FakeStock(videos={"stormy ocean aerial": [cand("a"), cand("b"), cand("c"), cand("d")]})
    s = make_selector(tmp_path, [stock])
    with ThreadPoolExecutor(2) as pool:
        s.pool = pool
        long_ = s.pick(scene(1), 8)
        short = s.pick(scene(2), 3)
        assets = s.finish([long_, short])
    assert assets[0].extra == ["visuals/scene_001b.mp4"] and assets[1].extra == []
    assert (tmp_path / "visuals" / "scene_001b.mp4").exists()
    assert "b" not in [x.uid for x in assets[0].alternates]  # the extra clip isn't offered as a swap
    assert assets[1].uid == "c"                              # nor reused for another scene


# --- query set, thumbnails, junk filter ------------------------------------------------------------
def test_fallback_queries_never_end_on_a_stopword():
    out = sel.fallback_queries("email with suspicious attachment")
    assert "email with" not in out and all(q.split()[-1] not in sel.STOPWORDS for q in out)
    assert sel.fallback_queries("person clicking on suspicious link")[1:] == ["person clicking", "suspicious link"]


def test_pexels_and_pixabay_parse_thumbnails(tmp_path):
    videos = {"videos": [{**PEXELS_VIDEOS["videos"][0], "image": "https://img/v1.jpg"}]}
    photos = {"photos": [{"id": 2, "width": 1080, "height": 1920, "url": "https://pexels.com/photo/x-2/",
                          "photographer": "Bo", "alt": "hand on phone",
                          "src": {"large2x": "https://img/big.jpg", "medium": "https://img/med.jpg"}}]}
    px = Pexels("k", tmp_path, transport({"https://api.pexels.com/videos": videos,
                                          "https://api.pexels.com/v1": photos}))
    assert px.videos("q", "portrait")[0].thumb == "https://img/v1.jpg"
    assert px.photos("q", "portrait")[0].thumb == "https://img/med.jpg"
    hits = {"hits": [{**PIXABAY_VIDEOS["hits"][0],
                      "videos": {**PIXABAY_VIDEOS["hits"][0]["videos"],
                                 "tiny": {"url": "https://x/t.mp4", "width": 640, "height": 360,
                                          "thumbnail": "https://img/pb.jpg"}}}]}
    pb = Pixabay("k", tmp_path / "pb", transport({"https://pixabay.com/api/videos": hits}))
    assert pb.videos("q", "portrait")[0].thumb == "https://img/pb.jpg"


def test_green_screen_and_mockups_are_never_picked(tmp_path, fake_download):
    stock = FakeStock(videos={"computer screen with fake login form": [
        tcand("green", "a computer monitor with a green screen on it"),
        tcand("login", "person typing login form on computer screen", 1920, 1080)]})
    asset = make_selector(tmp_path, [stock]).pick(scene(1, q="computer screen with fake login form"), 5)
    assert asset.uid == "login"
    assert "green" not in [a.uid for a in asset.alternates]


def test_scene_alt_queries_are_searched_together(tmp_path, fake_download):
    stock = FakeStock(videos={
        "person clicking suspicious link": [tcand("palm", "a close up of a person s hand")],
        "finger tapping link on phone screen": [tcand("tap", "finger tapping link on phone screen")]})
    sc = Scene(id=1, narration="n", visual_query="person clicking suspicious link",
               alt_queries=["finger tapping link on phone screen", "laptop email inbox"])
    asset = make_selector(tmp_path, [stock]).pick(sc, 5)
    assert asset.uid == "tap" and asset.query == "finger tapping link on phone screen"
    assert asset.alternates == [] or asset.alternates[0].uid == "palm"


def test_alternate_keeps_thumb():
    import dataclasses

    c = dataclasses.replace(tcand("a", "x"), thumb="https://img/a.jpg")
    assert sel._alternate(c).thumb == "https://img/a.jpg"
