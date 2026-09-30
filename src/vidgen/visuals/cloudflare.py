"""AI stills from Cloudflare Workers AI — the free daily allocation (10,000 neurons/day) covers ~170 FLUX.1
[schnell] images, and needs no local GPU (ComfyUI stays the first choice when it runs).

FLUX.2 [klein] models take multipart form fields and any width/height from 256 to 1920 (a true 9:16 frame);
FLUX.1 [schnell] takes JSON and always draws a square. Both answer {"result": {"image": <base64>}}.
Credentials: CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN (a token with Workers AI permission) in .env.
"""

import base64
import logging
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
SIGNATURES = {b"\xff\xd8\xff": ".jpg", b"\x89PNG\r\n\x1a\n": ".png", b"RIFF": ".webp"}


class CloudflareError(RuntimeError):
    pass


class CloudflareImages:
    name = "cloudflare"

    def __init__(self, account_id: str, token: str, model: str, size: tuple[int, int], timeout: float = 120):
        self.account_id, self.token, self.model, self.size, self.timeout = account_id, token, model, size, timeout

    def _request(self, prompt: str) -> dict:
        kw: dict = {"headers": {"Authorization": f"Bearer {self.token}"}, "timeout": self.timeout}
        if "flux-2" in self.model:   # multipart: every field as a form part
            w, h = (min(1920, max(256, v)) for v in self.size)
            kw["files"] = {k: (None, str(v)) for k, v in {"prompt": prompt, "width": w, "height": h}.items()}
        else:
            kw["json"] = {"prompt": prompt, "steps": 4}
        return kw

    def image(self, prompt: str, out: Path) -> Path:
        url = API.format(account=self.account_id, model=self.model)
        resp = httpx.post(url, **self._request(prompt))
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code != 200 or not body.get("success", False):
            errors = "; ".join(e.get("message", "") for e in body.get("errors", [])) or resp.text[:200]
            raise CloudflareError(f"Workers AI {self.model} {resp.status_code}: {errors}")
        try:
            data = base64.b64decode(body["result"]["image"])
        except (KeyError, TypeError, ValueError) as e:
            raise CloudflareError(f"Workers AI {self.model}: no image in the answer") from e
        ext = next((x for sig, x in SIGNATURES.items() if data.startswith(sig)), None)
        if ext is None:
            raise CloudflareError(f"Workers AI {self.model}: the answer is not an image")
        path = out.with_suffix(ext)
        path.write_bytes(data)
        log.info("cloudflare %s → %s (%d KB)", self.model, path.name, len(data) // 1024)
        return path
