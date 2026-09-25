"""Per-scene visual sourcing with a fallback chain.

  ai_video scene : Wan (budgeted) → Flux still → stock chain
  ai_image scene : Flux → stock chain
  stock chain    : stock video → stock photo → Flux → color placeholder
"""

import logging
import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx

from vidgen.config import FormatPreset, Settings
from vidgen.models import Alternate, Asset, Scene, Script, Timeline
from vidgen.visuals.stock import Candidate, Pexels, Pixabay, StockClient, download

log = logging.getLogger(__name__)
MIN_SHORT_SIDE = 720


MAX_ALTERNATES = 3


class SwapError(RuntimeError):
    pass


def _alternate(c: Candidate | Alternate) -> Alternate:
    if isinstance(c, Alternate):
        return c
    return Alternate(uid=c.uid, kind=c.kind, download_url=c.download_url, page_url=c.page_url,
                     width=c.width, height=c.height, duration=c.duration, author=c.author,
                     source=c.source, license=c.license)


def score(c: Candidate, orientation: str, scene_seconds: float) -> int:
    s = 3 if (c.height > c.width) == (orientation == "portrait") else 0
    s += 2 if min(c.width, c.height) >= MIN_SHORT_SIDE else 0
    if c.kind == "video":
        s += 2 if c.duration >= scene_seconds else 0
    return s


def fallback_queries(query: str) -> list[str]:
    """Each comma-separated idea on its own (LLMs often list several), then shorter forms of the first:
    stock search is literal and misses on long phrases."""
    parts = [p.strip() for p in re.split(r"[,;]", query) if p.strip()] or [query]
    out = list(parts)
    words = parts[0].split()
    if len(words) > 2:
        out.append(" ".join(words[:2]))
    if len(words) > 1:
        out.append(words[-1])
    return list(dict.fromkeys(out))


def _link_or_copy(src: Path, dest: Path) -> None:
    dest.unlink(missing_ok=True)
    try:
        os.link(src, dest)  # same drive: no extra disk space for 100-scene videos
    except OSError:
        shutil.copy2(src, dest)


class Selector:
    def __init__(self, clients: list[StockClient], ai, preset: FormatPreset, out_dir: Path,
                 cache_dir: Path, http: httpx.Client | None = None):
        self.clients = clients
        self.ai = ai  # AIGenerator or None when ComfyUI is offline
        self.orientation = preset.orientation
        self.ai_video_budget = preset.max_ai_video
        self.visuals_dir = out_dir / "visuals"
        self.cache_dir = cache_dir
        self.http = http or httpx.Client(timeout=120, follow_redirects=True)
        self.used: set[str] = set()

    def pick(self, scene: Scene, seconds: float) -> Asset:
        self.visuals_dir.mkdir(parents=True, exist_ok=True)
        steps: list[Callable[[], Asset | None]] = []
        if scene.visual_type == "ai_video" and self.ai_video_budget > 0:
            steps.append(lambda: self._ai(scene, video=True))
        if scene.visual_type in ("ai_video", "ai_image"):
            steps.append(lambda: self._ai(scene, video=False))
        steps += [lambda: self._stock(scene, seconds, "video"),
                  lambda: self._stock(scene, seconds, "image")]
        if scene.visual_type == "stock":
            steps.append(lambda: self._ai(scene, video=False))
        for step in steps:
            asset = step()
            if asset:
                log.info("scene %d: %s %s (%s)", scene.id, asset.source, asset.kind, scene.visual_query)
                return asset
        log.warning("scene %d: nothing found for %r → placeholder", scene.id, scene.visual_query)
        return Asset(scene_id=scene.id, path="", kind="color", source="placeholder")

    def _ai(self, scene: Scene, video: bool) -> Asset | None:
        if self.ai is None:
            return None
        prompt = scene.ai_prompt or f"photorealistic cinematic shot of {scene.visual_query}, natural lighting"
        out = self.visuals_dir / f"scene_{scene.id:03d}"
        try:
            path = self.ai.video(prompt, out) if video else self.ai.image(prompt, out)
        except Exception as e:  # ComfyUI errors, missing models, OOM → fall through the chain
            log.warning("scene %d: AI %s failed: %s", scene.id, "video" if video else "image", e)
            return None
        if video:
            self.ai_video_budget -= 1
        return Asset(scene_id=scene.id, path=f"visuals/{path.name}", kind="video" if video else "image",
                     source="wan" if video else "flux", license="AI-generated")

    def _stock(self, scene: Scene, seconds: float, kind: str, query_text: str | None = None) -> Asset | None:
        for query in fallback_queries(query_text or scene.visual_query):
            candidates: list[Candidate] = []
            for client in self.clients:
                try:
                    candidates += client.videos(query, self.orientation) if kind == "video" \
                        else client.photos(query, self.orientation)
                except httpx.HTTPError as e:
                    log.warning("%s search failed for %r: %s", client.source, query, e)
            ranked = sorted((c for c in candidates if c.uid not in self.used),
                            key=lambda c: score(c, self.orientation, seconds), reverse=True)
            while ranked:
                best, ranked = ranked[0], ranked[1:]
                asset = self._use(scene, best, query, ranked[:MAX_ALTERNATES])
                if asset:
                    return asset
        return None

    def _use(self, scene: Scene, c: Candidate | Alternate, query: str,
             alternates: list[Candidate | Alternate]) -> Asset | None:
        """Download a candidate into visuals/scene_NNN.ext; keep the next-best ones for later swaps."""
        self.used.add(c.uid)
        try:
            cached = download(c.download_url, self.cache_dir, self.http)
        except httpx.HTTPError as e:
            log.warning("download failed %s: %s", c.download_url, e)
            return None
        dest = self.visuals_dir / f"scene_{scene.id:03d}{cached.suffix}"
        _link_or_copy(cached, dest)
        return Asset(scene_id=scene.id, path=f"visuals/{dest.name}", kind=c.kind, source=c.source,
                     url=c.page_url, author=c.author, license=c.license, uid=c.uid, query=query,
                     alternates=[_alternate(a) for a in alternates])

    def swap(self, scene: Scene, seconds: float, current: Asset, used: set[str],
             query: str | None = None) -> Asset:
        """Another clip for one scene, never one already used anywhere in the video.
        No query: next saved alternate, else a fresh search on the scene's query. Query: search it."""
        self.visuals_dir.mkdir(parents=True, exist_ok=True)
        self.used = set(used) | ({current.uid} if current.uid else set())
        if not query:
            pending = [a for a in current.alternates if a.uid not in self.used]
            while pending:
                alt, pending = pending[0], pending[1:]
                asset = self._use(scene, alt, current.query or scene.visual_query, pending)
                if asset:
                    return asset
        for kind in ("video", "image"):
            asset = self._stock(scene, seconds, kind, query or current.query or scene.visual_query)
            if asset:
                return asset
        raise SwapError("Không tìm được clip khác — thử từ khóa khác")


def stock_clients(s: Settings) -> list[StockClient]:
    cache = s.path(s.pipeline.cache_dir)
    clients: list[StockClient] = []
    if s.secrets.pexels_api_key:
        clients.append(Pexels(s.secrets.pexels_api_key, cache))
    if s.secrets.pixabay_api_key:
        clients.append(Pixabay(s.secrets.pixabay_api_key, cache))
    return clients


def build_selector(script: Script, preset: FormatPreset, out_dir: Path, s: Settings) -> Selector:
    from vidgen.visuals.ai import AIGenerator
    from vidgen.visuals.comfy import ComfyClient

    cache = s.path(s.pipeline.cache_dir)
    clients = stock_clients(s)
    comfy = ComfyClient(s.secrets.comfyui_url)
    ai = AIGenerator(comfy, s, script.format) if comfy.available() else None
    if not clients and ai is None:
        log.warning("no stock API keys and ComfyUI offline — every scene will be a placeholder")
    return Selector(clients, ai, preset, out_dir, cache)


def source_visuals(script: Script, timeline: Timeline, preset: FormatPreset, out_dir: Path,
                   s: Settings, selector: Selector | None = None) -> list[Asset]:
    selector = selector or build_selector(script, preset, out_dir, s)
    seconds = {sa.scene_id: sa.duration for sa in timeline.scenes}
    try:
        return [selector.pick(scene, seconds.get(scene.id, 5.0)) for scene in script.scenes]
    finally:
        if selector.ai is not None:
            selector.ai.client.free()
