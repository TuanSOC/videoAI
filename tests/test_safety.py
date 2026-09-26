"""Data-safety guarantees: a failure never leaves a half-written artifact counted as done, never throws
away finished work, and one bad input never takes down a whole stage."""

import json
import threading

import httpx
import pytest

from vidgen import pipeline
from vidgen.config import get_settings
from vidgen.models import Angle, Brief, Scene, Script, SourceDoc
from vidgen.script import writer
from vidgen.script.llm import LLMChain
from vidgen.visuals import stock


def script_obj(narration="Cũ."):
    return Script(title="t", hook="h", lang="vi", format="short",
                  scenes=[Scene(id=1, narration=narration, visual_query="q")])


# --- script regeneration ---------------------------------------------------------------------------
def brief_obj():
    src = lambda n: SourceDoc(title=n, url=f"https://x/{n}", text="t", lang="vi")  # noqa: E731
    return Brief(topic="t", angles=[Angle(style="explain", title="A0", hook="h0", key_points=["k"], sources=[src("a")]),
                                    Angle(style="myth", title="A1", hook="h1", key_points=["k"], sources=[src("b")])],
                 chosen=0)


def test_failed_regeneration_keeps_the_finished_video_and_its_angle(tmp_path, monkeypatch):
    (tmp_path / "state.json").write_text('{"topic": "t", "format": "short", "lang": "vi"}', encoding="utf-8")
    (tmp_path / pipeline.BRIEF_FILE).write_text(brief_obj().model_dump_json(), encoding="utf-8")
    (tmp_path / "script.json").write_text(script_obj().model_dump_json(), encoding="utf-8")
    monkeypatch.setattr("vidgen.script.writer.generate", lambda *a, **k: script_obj())
    monkeypatch.setattr(pipeline, "check_facts", lambda d, s: None)
    pipeline.write_script(tmp_path, get_settings())                       # first script from angle 0
    for name in ("timeline.json", "assets.json", "final.mp4", "metadata.json"):
        (tmp_path / name).write_text("done")
    before = (tmp_path / "script.json").read_text(encoding="utf-8")

    pipeline.choose_angle(tmp_path, 1)                                     # user picks another angle...

    def down(*a, **k):
        raise RuntimeError("Ollama down")
    monkeypatch.setattr("vidgen.script.writer.generate", down)
    with pytest.raises(RuntimeError):
        pipeline.write_script(tmp_path, get_settings())                   # ...and the LLM fails

    assert (tmp_path / "script.json").read_text(encoding="utf-8") == before
    assert all((tmp_path / n).exists() for n in ("timeline.json", "assets.json", "final.mp4", "metadata.json"))
    brief = pipeline.load_brief(tmp_path)
    assert brief.chosen_angle().title == "A0"                            # facts still match the script
    assert [s.title for s in brief.chosen_sources()] == ["a"]


def test_successful_regeneration_replaces_everything(tmp_path, monkeypatch):
    (tmp_path / "state.json").write_text('{"topic": "t", "format": "short", "lang": "vi"}', encoding="utf-8")
    (tmp_path / pipeline.BRIEF_FILE).write_text(brief_obj().model_dump_json(), encoding="utf-8")
    for name in ("timeline.json", "final.mp4"):
        (tmp_path / name).write_text("old")
    monkeypatch.setattr("vidgen.script.writer.generate", lambda *a, **k: script_obj("Mới."))
    monkeypatch.setattr(pipeline, "check_facts", lambda d, s: None)
    pipeline.write_script(tmp_path, get_settings())
    assert not (tmp_path / "final.mp4").exists() and "Mới." in (tmp_path / "script.json").read_text(encoding="utf-8")
    assert pipeline.load_brief(tmp_path).script_angle.title == "A0"


# --- render ------------------------------------------------------------------------------------------
def test_failed_final_encode_leaves_no_final_mp4(tmp_path, monkeypatch):
    from vidgen.assemble import render
    from vidgen.models import SceneAudio, Timeline, WordTiming

    def fake_run(args, cwd=None, timeout=None):
        (cwd / args[-1]).write_bytes(b"half")      # ffmpeg writes part of the output, then dies
        raise render.ffmpeg.FFmpegError("boom")
    monkeypatch.setattr(render, "MUSIC_DIR", tmp_path / "no-music")
    monkeypatch.setattr(render, "render_segments", lambda *a, **k: ([], []))
    monkeypatch.setattr(render.ffmpeg, "run", fake_run)
    (tmp_path / "segments").mkdir()
    tl = Timeline(scenes=[SceneAudio(scene_id=1, path="", start=0, duration=1,
                                     words=[WordTiming(word="Cũ", start=0, end=.5)])])
    with pytest.raises(render.ffmpeg.FFmpegError):
        render.render_video(script_obj(), tl, [], get_settings().preset("short"), tmp_path, "s", sfx_density=None)
    assert not (tmp_path / "final.mp4").exists()


def test_old_folder_gets_its_script_hash_even_when_nothing_runs(tmp_path, monkeypatch):
    stages = [pipeline.Stage(st.name, st.artifact, st.extra, lambda job: None) for st in pipeline.STAGES]
    monkeypatch.setattr(pipeline, "STAGES", stages)
    (tmp_path / "script.json").write_text(script_obj().model_dump_json(), encoding="utf-8")
    for st in stages:
        (tmp_path / st.artifact).write_text("x")
    (tmp_path / "state.json").write_text('{"topic": "t"}', encoding="utf-8")   # legacy: no hash
    pipeline.run_stages(tmp_path, get_settings())
    assert json.loads((tmp_path / "state.json").read_text(encoding="utf-8")).get("script_hash")


# --- stock -------------------------------------------------------------------------------------------
def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_malformed_hits_are_skipped(tmp_path):
    data = {"photos": [{"id": 1}, {"id": 2, "width": 1080, "height": 1920, "url": "u",
                                    "src": {"large2x": "https://x/2.jpg", "medium": "m"}}]}
    px = stock.Pexels("k", tmp_path, client(lambda r: httpx.Response(200, json=data)))
    assert [c.uid for c in px.photos("q", "portrait")] == ["pexels:p2"]


def test_corrupt_cache_file_is_refetched(tmp_path):
    calls = []

    def handler(r):
        calls.append(r)
        return httpx.Response(200, json={"photos": []})
    px = stock.Pexels("k", tmp_path, client(handler))
    px.photos("q", "portrait")
    for f in (tmp_path / "search").glob("*.json"):
        f.write_text('{"photos": [', encoding="utf-8")     # truncated by a crash
    assert px.photos("q", "portrait") == [] and len(calls) == 2


def test_non_json_reply_and_date_retry_after_are_http_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(stock.time, "sleep", lambda s: None)
    replies = iter([httpx.Response(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}),
                    httpx.Response(200, text="<html>captive portal</html>")])
    px = stock.Pexels("k", tmp_path, client(lambda r: next(replies)))
    with pytest.raises(httpx.HTTPError):
        px.photos("q", "portrait")


def test_same_url_downloaded_twice_at_once(tmp_path):
    gate = threading.Barrier(2)

    class Slow:
        def __init__(self, r):
            self.r = r

        def iter_bytes(self, n):
            gate.wait(timeout=5)
            yield b"data"

        def raise_for_status(self):
            pass

    class Http:
        def stream(self, method, url):
            import contextlib
            return contextlib.nullcontext(Slow(None))

    errors, paths = [], []

    def go():
        try:
            paths.append(stock.download("https://x/a.mp4", tmp_path, Http()))
        except Exception as e:  # noqa: BLE001
            errors.append(e)
    threads = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == [] and len(set(paths)) == 1 and paths[0].read_bytes() == b"data"


# --- script side ---------------------------------------------------------------------------------------
class Replies:
    name = "seq"

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def generate_json(self, prompt, schema):
        self.prompts.append(prompt)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r)


def test_one_failed_fact_check_batch_keeps_the_others():
    from vidgen.script.factcheck import SCENES_PER_CHECK, fact_check
    from vidgen.script.research import Source

    s = Script(title="t", hook="h", lang="vi", format="long",
               scenes=[Scene(id=i, narration=f"Câu {i}.", visual_query="q") for i in range(1, SCENES_PER_CHECK + 3)])
    llm = LLMChain([Replies({"issues": [{"id": 3, "note": "sai"}]}, "not json", "not json", "not json")])
    result = fact_check(s, [Source(title="S", url="u", text="t", lang="vi")], llm)
    assert result.checked and [i["scene_id"] for i in result.issues] == [3]


def test_rewrite_keeps_the_first_answer_when_the_retry_fails():
    long = {"narration": " ".join(["dài"] * 30) + ".", "visual_query": "q"}
    llm = LLMChain([Replies(long, "x", "x", "x")])
    out = writer.rewrite_one(script_obj(), script_obj().scenes[0], "", "", None, [], None, llm)
    assert out.narration.startswith("dài")


def test_a_failed_chapter_does_not_lose_the_whole_long_video():
    outline = {"title": "T", "hook": "Hook here.", "hook_visual_query": "q",
               "chapters": [{"title": "A", "summary": "a"}, {"title": "B", "summary": "b"},
                            {"title": "C", "summary": "c"}]}
    chapter = {"scenes": [{"narration": " ".join(["w"] * 20) + ".", "visual_query": "q"}]}   # 1 scene is fine
    bad = "not json"
    llm = LLMChain([Replies(outline, chapter, bad, bad, bad, chapter)])
    # tiny target: every 20-word chapter is long enough, so no expansion call is involved
    preset = get_settings().preset("long").model_copy(update={"target_seconds": (10, 12)})
    script = writer.generate("x", "long", "en", preset, llm)
    assert [s.chapter for s in script.scenes].count("B") == 0 and script.scenes[-1].chapter == "C"
