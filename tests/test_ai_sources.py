"""Cinematic visuals, phase 3: AI stills from local ComfyUI when it runs, else Cloudflare Workers AI (free daily
allocation). No network here: httpx.post is replaced."""

import base64
import json

import httpx
import pytest

from vidgen.config import get_settings
from vidgen.visuals import cloudflare as cf
from vidgen.visuals.ai import ImageSources

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def ok(image=JPEG):
    return httpx.Response(200, json={"success": True, "result": {"image": base64.b64encode(image).decode()}})


def fake_post(monkeypatch, *responses):
    calls, queue = [], list(responses)

    def post(url, **kw):
        calls.append((url, kw))
        return queue.pop(0)
    monkeypatch.setattr(cf.httpx, "post", post)
    return calls


def test_flux2_klein_is_asked_for_a_vertical_frame_as_multipart(monkeypatch, tmp_path):
    calls = fake_post(monkeypatch, ok())
    gen = cf.CloudflareImages("acc", "tok", "@cf/black-forest-labs/flux-2-klein-4b", (768, 1344))
    out = gen.image("Low angle of a cracked padlock, rim light", tmp_path / "scene_001")
    assert out == tmp_path / "scene_001.jpg" and out.read_bytes() == JPEG
    url, kw = calls[0]
    assert url == "https://api.cloudflare.com/client/v4/accounts/acc/ai/run/@cf/black-forest-labs/flux-2-klein-4b"
    assert kw["headers"]["Authorization"] == "Bearer tok"
    form = {k: v[1] for k, v in kw["files"].items()}
    assert form["prompt"] == "Low angle of a cracked padlock, rim light"
    assert (form["width"], form["height"]) == ("768", "1344")


def test_flux1_schnell_takes_json_and_the_format_follows_the_bytes(monkeypatch, tmp_path):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    calls = fake_post(monkeypatch, ok(png))
    out = cf.CloudflareImages("acc", "tok", "@cf/black-forest-labs/flux-1-schnell", (768, 1344)).image("x", tmp_path / "s")
    assert out.suffix == ".png"
    assert calls[0][1]["json"] == {"prompt": "x", "steps": 4}


@pytest.mark.parametrize("resp", [httpx.Response(429, json={"success": False, "errors": [{"message": "daily limit"}]}),
                                  httpx.Response(200, json={"success": False, "errors": [{"message": "bad"}]}),
                                  httpx.Response(200, json={"success": True, "result": {"image": "bm90IGFuIGltYWdl"}})])
def test_errors_and_non_images_raise(monkeypatch, tmp_path, resp):
    fake_post(monkeypatch, resp)
    with pytest.raises(cf.CloudflareError):
        cf.CloudflareImages("acc", "tok", "@cf/black-forest-labs/flux-2-klein-4b", (768, 1344)).image("x", tmp_path / "s")


class Source:
    def __init__(self, name, fail=False):
        self.name, self.fail, self.calls = name, fail, 0

    def image(self, prompt, out):
        self.calls += 1
        if self.fail:
            raise RuntimeError(f"{self.name} down")
        p = out.with_suffix(".png")
        p.write_bytes(b"png")
        return p


def test_sources_are_tried_in_order_and_the_one_that_drew_is_recorded(tmp_path):
    comfy, cloud = Source("flux", fail=True), Source("cloudflare")
    chain = ImageSources([comfy, cloud])
    assert chain.image("p", tmp_path / "a").exists() and chain.last == "cloudflare" and comfy.calls == 1
    with pytest.raises(RuntimeError):
        ImageSources([Source("flux", fail=True)]).image("p", tmp_path / "b")
    with pytest.raises(RuntimeError):
        chain.video("p", tmp_path / "c")          # AI video needs ComfyUI (Wan)
    chain.client.free()                          # nothing to unload without ComfyUI: no error


def test_build_uses_whatever_is_available(monkeypatch):
    from vidgen.visuals import ai
    s = get_settings().model_copy(deep=True)
    s.secrets.cloudflare_account_id, s.secrets.cloudflare_api_token = "acc", "tok"
    monkeypatch.setattr(ai.ComfyClient, "available", lambda self: False)
    chain = ai.build_sources(s, "short")
    assert [x.name for x in chain.sources] == ["cloudflare"] and chain.sources[0].size == (768, 1344)
    s.secrets.cloudflare_api_token = ""
    assert ai.build_sources(s, "short") is None
    s.pipeline.ai.image_sources = ["cloudflare"]
    s.secrets.cloudflare_api_token = "tok"
    monkeypatch.setattr(ai.ComfyClient, "available", lambda self: True)
    assert [x.name for x in ai.build_sources(s, "long").sources] == ["cloudflare"]


def test_a_drawn_scene_is_labelled_by_its_source_and_disclosed(tmp_path):
    from vidgen.metadata import generate_metadata
    from vidgen.models import Scene, Script
    from vidgen.script.llm import LLMChain
    from vidgen.visuals import selector as sel
    s = sel.Selector([], ImageSources([Source("cloudflare")]), get_settings().preset("short"), tmp_path, tmp_path / "c")
    asset = s.pick(Scene(id=1, narration="n", visual_query="q", visual_type="ai_image", ai_prompt="note"), 5)
    assert asset.source == "cloudflare" and asset.license == "AI-generated"

    class Meta:
        name = "fake"

        def generate_json(self, prompt, schema):
            return json.dumps({"title": "T", "description": "D", "tags": ["a"], "hashtags": ["#a"]})
    script = Script(title="t", hook="h", lang="en", format="short", scenes=[Scene(id=1, narration="n", visual_query="q")])
    meta = generate_metadata(script, [asset], LLMChain([Meta()]))
    assert meta.ai_visuals_used is True
