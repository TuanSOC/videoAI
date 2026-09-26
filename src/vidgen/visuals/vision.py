"""Look at candidate clips before choosing one: a local vision model (Ollama, qwen2.5vl) scores each
stock thumbnail against what the scene says. Text matching alone picked a green screen for "computer
screen with fake login form" and a bare palm for "person clicking a suspicious link".

8 GB of VRAM holds one model at a time: the script model is unloaded before sourcing, the vision model
stays loaded across scenes (keep_alive) and is unloaded afterwards. Any failure (model missing, Ollama
down, bad JSON) means "no opinion" and the selector keeps its text ranking — never a failed job.
"""

import base64
import logging
import time

import cv2
import httpx
import numpy as np
from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)
TILE = 320             # each candidate is fitted into a TILE×TILE square of one numbered strip:
                       # Ollama upscales small images, so separate ones cost ~1.1k tokens each (5 → 5.5k,
                       # spilling the model onto the CPU); one strip of 5 costs ~1.2k and scored better
TIMEOUT = 120          # the first call also loads the model
TIMEOUT_LOADED = 45    # later calls: the model is in VRAM already
MAX_FAILURES = 2       # consecutive failures before the judge gives up for this video
UNLOAD_WAIT = 20       # seconds to wait for an unloaded model to actually leave VRAM
KEEP_ALIVE = "3m"      # stay loaded between scenes

PROMPT = """You choose stock footage for one scene of a short video.
Narration of the scene: "{narration}"
The editor searched for: {queries}

The image is a strip of {n} numbered candidate frames (1 to {n}, left to right). Score each from 0 to 10:
- 10: clearly shows what the narration talks about, looks like real footage
- 5: loosely related, a generic but acceptable illustration
- 0: unrelated, a green screen, a template/mockup, mostly text or a logo, or a staged cliché
Judge the picture, not the search words. Return one score per frame, for frames 1 to {n}."""


class VisionScores(BaseModel):
    scores: list[int]


def _tile(image: bytes, number: int) -> np.ndarray | None:
    img = cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        return None
    h, w = img.shape[:2]
    k = TILE / max(h, w)
    img = cv2.resize(img, (max(1, round(w * k)), max(1, round(h * k))), interpolation=cv2.INTER_AREA)
    out = np.zeros((TILE, TILE, 3), np.uint8)
    y, x = (TILE - img.shape[0]) // 2, (TILE - img.shape[1]) // 2
    out[y:y + img.shape[0], x:x + img.shape[1]] = img
    cv2.rectangle(out, (0, 0), (54, 54), (0, 0, 0), -1)
    cv2.putText(out, str(number), (12, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 3)
    return out


def strip(images: list[bytes]) -> bytes | None:
    """Candidates side by side in numbered squares, as one JPEG."""
    tiles = [_tile(img, i + 1) for i, img in enumerate(images)]
    if any(t is None for t in tiles):
        return None
    return cv2.imencode(".jpg", np.hstack(tiles), [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()


class VisionJudge:
    def __init__(self, url: str, model: str, http: httpx.Client | None = None, unload_first: str | None = None):
        self.url = url.rstrip("/")
        self.model = model
        self.http = http or httpx.Client()
        self.unload_first = unload_first  # model to free from VRAM before this one loads
        self.used = False       # a request was sent (the model may be loaded)
        self.loaded = False     # it answered at least once
        self.failures = 0
        self.disabled = False   # model missing or keeps failing: stop asking for every scene

    def score(self, narration: str, queries: list[str], images: list[bytes]) -> list[int] | None:
        """One 0-10 score per image, or None (no opinion)."""
        if self.disabled or not images:
            return None
        picture = strip(images)
        if picture is None:
            return None
        prompt = PROMPT.format(narration=narration, queries=", ".join(f'"{q}"' for q in queries), n=len(images))
        if not self.used and self.unload_first:
            unload(self.url, self.unload_first, http=self.http)
        self.used = True
        try:
            resp = self.http.post(f"{self.url}/api/chat", json={
                "model": self.model,
                "messages": [{"role": "user", "content": prompt,
                              "images": [base64.b64encode(picture).decode()]}],
                "format": VisionScores.model_json_schema(),
                "stream": False,
                "keep_alive": KEEP_ALIVE,
                "options": {"temperature": 0, "num_ctx": 4096},  # strip ≈ 1.2k tokens; 8k spilled onto the CPU
            }, timeout=TIMEOUT_LOADED if self.loaded else TIMEOUT)
        except httpx.HTTPError as e:
            log.warning("vision judge unavailable: %s", e)
            return self._failed()
        if resp.status_code != 200:
            if "not found" in resp.text:
                self.disabled = True
                log.warning("vision model %s is not installed — run: ollama pull %s (using text matching)",
                            self.model, self.model)
            else:
                log.warning("vision judge error %s: %s", resp.status_code, resp.text[:200])
            return self._failed()
        self.loaded = True
        try:
            scores = VisionScores.model_validate_json(resp.json()["message"]["content"]).scores
        except (ValidationError, KeyError, ValueError) as e:
            log.warning("vision judge gave an unreadable answer: %s", e)
            return self._failed()
        scores = scores[:len(images)]  # seen live: [0, 0, 0] for a single image
        if len(scores) != len(images) or any(not 0 <= s <= 10 for s in scores):
            log.warning("vision judge returned %s for %d images", scores, len(images))
            return self._failed()
        self.failures = 0
        return scores

    def _failed(self) -> None:
        """No opinion this time; after MAX_FAILURES in a row (OOM, stuck Ollama) stop asking."""
        self.failures += 1
        if self.failures >= MAX_FAILURES and not self.disabled:
            self.disabled = True
            log.warning("vision judge failed %d times in a row — using text matching for the rest", self.failures)
        return None


def unload(url: str, model: str, http: httpx.Client | None = None) -> None:
    """Free the model's VRAM now instead of after Ollama's idle timeout, and wait until it is gone:
    Ollama answers at once but evicts later, and a model loaded meanwhile lands partly on the CPU
    (seen live: qwen2.5vl with 4.3 of 6.1 GB in VRAM, 120 s per judgement)."""
    http = http or httpx.Client(timeout=30)
    base = url.rstrip("/")
    try:
        http.post(f"{base}/api/generate", json={"model": model, "keep_alive": 0})
        deadline = time.monotonic() + UNLOAD_WAIT
        while time.monotonic() < deadline:
            loaded = {m.get("name") for m in http.get(f"{base}/api/ps").json().get("models", [])}
            if model not in loaded:
                return
            time.sleep(0.5)
        log.warning("%s still in VRAM after %ds", model, UNLOAD_WAIT)
    except (httpx.HTTPError, ValueError) as e:
        log.debug("could not unload %s: %s", model, e)
