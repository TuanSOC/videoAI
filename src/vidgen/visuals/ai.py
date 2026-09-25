"""AI visuals via local ComfyUI: Flux-schnell stills, Wan 2.2 TI2V image-to-video clips."""

import random
from pathlib import Path

from vidgen.config import ROOT, Settings
from vidgen.visuals.comfy import ComfyClient

WORKFLOWS = ROOT / "comfy_workflows"
NEGATIVE = "cartoon, illustration, text, watermark, logo, blurry, distorted face, extra fingers, low quality"


class AIGenerator:
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
