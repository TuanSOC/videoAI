"""AI visuals: stills from local ComfyUI (Flux schnell) when it runs, else Cloudflare Workers AI; video clips
(Wan 2.2 TI2V) only from ComfyUI."""

import logging
import random
from pathlib import Path

from vidgen.config import ROOT, Settings
from vidgen.visuals.comfy import ComfyClient

log = logging.getLogger(__name__)

WORKFLOWS = ROOT / "comfy_workflows"
NEGATIVE = "cartoon, illustration, text, watermark, logo, blurry, distorted face, extra fingers, low quality"


class AIGenerator:
    name = "flux"

    def __init__(self, client: ComfyClient, s: Settings, fmt: str):
        self.client = client
        self.cfg = s.pipeline.ai
        self.fmt = fmt

    def image(self, prompt: str, out: Path, seed: int | None = None) -> Path:
        w, h = self.cfg.image_size[self.fmt]
        return self.client.run(WORKFLOWS / "flux_schnell.json", {
            "PROMPT": prompt, "WIDTH": w, "HEIGHT": h,
            "SEED": seed if seed is not None else random.randrange(2**31),
            "PREFIX": "vidgen/img",
        }, out, self.cfg.timeout_seconds)

    def video(self, prompt: str, out: Path, seed: int | None = None) -> Path:
        """Flux still first (better composition control), then animate it with Wan 2.2."""
        still = self.image(prompt, out.with_name(out.stem + "_still.png"), seed)
        w, h = self.cfg.video_size[self.fmt]
        return self.client.run(WORKFLOWS / "wan22_ti2v.json", {
            "PROMPT": prompt + ", subtle natural motion, cinematic", "NEGATIVE": NEGATIVE,
            "IMAGE": self.client.upload_image(still), "WIDTH": w, "HEIGHT": h,
            "FRAMES": self.cfg.video_frames,
            "SEED": seed if seed is not None else random.randrange(2**31),
            "PREFIX": "vidgen/vid",
        }, out, self.cfg.timeout_seconds)


class _NoClient:
    def free(self) -> None:  # nothing loaded on a local GPU
        pass


class ImageSources:
    """Stills from the first source that answers (ComfyUI, then Cloudflare); `last` names the one that drew.
    Video needs ComfyUI's Wan workflow."""

    def __init__(self, sources: list, comfy: AIGenerator | None = None):
        self.sources, self.comfy, self.last = sources, comfy, ""
        self.client = comfy.client if comfy is not None else _NoClient()   # vision_session frees its VRAM

    def image(self, prompt: str, out: Path) -> Path:
        error: Exception | None = None
        for source in self.sources:
            try:
                path = source.image(prompt, out)
                self.last = source.name
                return path
            except Exception as e:  # offline, daily allocation spent, bad token → next source
                log.warning("AI image via %s failed: %s", source.name, e)
                error = e
        raise error or RuntimeError("no AI image source")

    def video(self, prompt: str, out: Path) -> Path:
        if self.comfy is None:
            raise RuntimeError("AI video needs ComfyUI (Wan 2.2)")
        self.last = "wan"
        return self.comfy.video(prompt, out)


def build_sources(s: Settings, fmt: str) -> ImageSources | None:
    """The configured image sources that are usable now (ComfyUI answering, Cloudflare credentials set)."""
    from vidgen.visuals.cloudflare import CloudflareImages

    cfg, sec = s.pipeline.ai, s.secrets
    sources, comfy = [], None
    for name in cfg.image_sources:
        if name == "comfyui":
            client = ComfyClient(sec.comfyui_url)
            if client.available():
                comfy = AIGenerator(client, s, fmt)
                sources.append(comfy)
        elif name == "cloudflare" and sec.cloudflare_account_id and sec.cloudflare_api_token:
            sources.append(CloudflareImages(sec.cloudflare_account_id, sec.cloudflare_api_token,
                                            cfg.cloudflare_model, tuple(cfg.image_size[fmt])))
    return ImageSources(sources, comfy) if sources else None
