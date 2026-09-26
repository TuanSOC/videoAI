"""Pexels + Pixabay search → normalized candidates. Search responses are cached on disk to spare API quota
(Pexels: 200 req/hour). Both licenses allow free commercial use without attribution; we credit anyway."""

import hashlib
import json
import logging
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from vidgen.fsutil import replace_with_retry, write_atomic

log = logging.getLogger(__name__)
PER_PAGE = 30  # a video about one subject needs many distinct clips of it
MAX_DIM = 1920  # skip 4K variants: slower to download and decode, no visible gain at 1080p


@dataclass(frozen=True)
class Candidate:
    uid: str          # "pexels:123" — used to avoid showing the same clip twice
    kind: str         # video | image
    download_url: str
    page_url: str
    width: int
    height: int
    duration: float   # 0 for images
    author: str
    source: str
    license: str
    text: str = ""    # what the clip shows (Pexels page slug / alt text, Pixabay tags) for relevance
    thumb: str = ""   # small preview image, for the vision judge


def slug_text(page_url: str) -> str:
    """Pexels page urls describe the clip: /video/aerial-view-of-rough-ocean-waves-31755232/."""
    slug = page_url.rstrip("/").rsplit("/", 1)[-1]
    return " ".join(w for w in slug.split("-") if not w.isdigit())


class StockClient:
    source = ""
    license = ""

    def __init__(self, api_key: str, cache_dir: Path, http: httpx.Client | None = None):
        self.api_key = api_key
        self.cache_dir = cache_dir / "search"
        self.http = http or httpx.Client(timeout=30, follow_redirects=True)

    def _get(self, url: str, params: dict, headers: dict | None = None) -> dict:
        """JSON search reply, cached. Every failure surfaces as an httpx.HTTPError (the selector's
        "this search failed, try the next" signal) — a bad reply must not end the visuals stage."""
        key = hashlib.sha1(json.dumps([self.source, url, params], sort_keys=True).encode()).hexdigest()
        cached = self.cache_dir / f"{key}.json"
        if cached.exists():
            try:
                return json.loads(cached.read_text(encoding="utf-8"))
            except ValueError:  # cut short by a crash: fetch again
                cached.unlink(missing_ok=True)
        for attempt in range(2):
            resp = self.http.get(url, params=params, headers=headers)
            if resp.status_code == 429 and attempt == 0:
                wait = retry_after(resp.headers.get("Retry-After"), default=30, cap=60)
                log.warning("%s rate limited, waiting %ss", self.source, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        try:
            data = resp.json()
        except ValueError as e:  # a proxy or captive portal answering with HTML
            raise httpx.DecodingError(f"{self.source}: reply is not JSON", request=resp.request) from e
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        write_atomic(cached, json.dumps(data))
        return data

    def videos(self, query: str, orientation: str) -> list[Candidate]:
        raise NotImplementedError

    def photos(self, query: str, orientation: str) -> list[Candidate]:
        raise NotImplementedError


def pick_file(files: list[dict], w_key="width", h_key="height") -> dict | None:
    """Largest variant within MAX_DIM; otherwise the smallest one available."""
    usable = [f for f in files if f.get("url") or f.get("link")]
    within = [f for f in usable if max(f.get(w_key) or 0, f.get(h_key) or 0) <= MAX_DIM]
    if within:
        return max(within, key=lambda f: (f.get(w_key) or 0) * (f.get(h_key) or 0))
    return min(usable, key=lambda f: (f.get(w_key) or 0) * (f.get(h_key) or 0), default=None)


class Pexels(StockClient):
    source = "pexels"
    license = "Pexels License"

    def _headers(self) -> dict:
        return {"Authorization": self.api_key}

    def videos(self, query, orientation):
        data = self._get("https://api.pexels.com/videos/search",
                         {"query": query, "orientation": orientation, "per_page": PER_PAGE},
                         self._headers())

        def build(v: dict) -> Candidate | None:
            f = pick_file([x for x in v.get("video_files", []) if x.get("file_type") == "video/mp4"])
            if not f:
                return None
            return Candidate(f"pexels:v{v['id']}", "video", f["link"], v.get("url", ""),
                             f.get("width") or v["width"], f.get("height") or v["height"],
                             float(v.get("duration") or 0), (v.get("user") or {}).get("name", ""),
                             self.source, self.license, slug_text(v.get("url", "")), v.get("image", ""))
        return _hits(data.get("videos"), build, self.source)

    def photos(self, query, orientation):
        data = self._get("https://api.pexels.com/v1/search",
                         {"query": query, "orientation": orientation, "per_page": PER_PAGE},
                         self._headers())
        return _hits(data.get("photos"), lambda p: Candidate(
            f"pexels:p{p['id']}", "image", p["src"]["large2x"], p.get("url", ""), p["width"], p["height"], 0.0,
            p.get("photographer", ""), self.source, self.license,
            f'{p.get("alt") or ""} {slug_text(p.get("url", ""))}'.strip(), p["src"].get("medium", "")), self.source)


class Pixabay(StockClient):
    source = "pixabay"
    license = "Pixabay Content License"

    def videos(self, query, orientation):
        data = self._get("https://pixabay.com/api/videos/",
                         {"key": self.api_key, "q": query[:100], "per_page": PER_PAGE, "safesearch": "true"})

        def build(h: dict) -> Candidate | None:
            f = pick_file(list((h.get("videos") or {}).values()))
            if not f:
                return None
            return Candidate(f"pixabay:v{h['id']}", "video", f["url"], h.get("pageURL", ""),
                             f.get("width") or 0, f.get("height") or 0, float(h.get("duration") or 0),
                             h.get("user", ""), self.source, self.license, h.get("tags", ""), _pixabay_thumb(h))
        return _hits(data.get("hits"), build, self.source)

    def photos(self, query, orientation):
        data = self._get("https://pixabay.com/api/",
                         {"key": self.api_key, "q": query[:100], "per_page": PER_PAGE, "image_type": "photo",
                          "orientation": "vertical" if orientation == "portrait" else "horizontal",
                          "safesearch": "true"})
        return _hits(data.get("hits"), lambda h: Candidate(
            f"pixabay:p{h['id']}", "image", h["largeImageURL"], h.get("pageURL", ""), h.get("imageWidth", 0),
            h.get("imageHeight", 0), 0.0, h.get("user", ""), self.source, self.license, h.get("tags", ""),
            h.get("webformatURL", "")), self.source)


def _pixabay_thumb(hit: dict) -> str:
    """Pixabay video hits carry a thumbnail per rendition (newer API); older ones only a picture id."""
    for v in hit.get("videos", {}).values():
        if v.get("thumbnail"):
            return v["thumbnail"]
    pid = hit.get("picture_id")
    return f"https://i.vimeocdn.com/video/{pid}_640x360.jpg" if pid else ""


def retry_after(value: str | None, default: float, cap: float) -> float:
    """Seconds from a Retry-After header, which may be a number or an HTTP date (then: default)."""
    try:
        return min(float(value), cap) if value else default
    except ValueError:
        return default


def _hits(items: list, build: Callable[[dict], "Candidate | None"], source: str) -> list[Candidate]:
    """Candidates from API hits; a hit missing a field is skipped, not fatal."""
    out = []
    for item in items or []:
        try:
            c = build(item)
        except (KeyError, TypeError, ValueError) as e:
            log.debug("%s: skipping malformed hit (%s)", source, e)
            continue
        if c is not None:
            out.append(c)
    return out


def url_suffix(url: str) -> str:
    path = url.split("?")[0]
    return ".mp4" if ".mp4" in path else Path(path).suffix or ".jpg"


def download(url: str, cache_dir: Path, http: httpx.Client) -> Path:
    """Download once into cache/files/, keyed by URL."""
    dest = cache_dir / "files" / (hashlib.sha1(url.encode()).hexdigest() + url_suffix(url))
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    # a temp name of its own: two jobs (or a swap and a job) may fetch the same clip at once
    fd, name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".part")
    tmp = Path(name)
    try:
        with http.stream("GET", url) as resp:
            resp.raise_for_status()
            with os.fdopen(fd, "wb") as fh:
                for chunk in resp.iter_bytes(1 << 16):
                    fh.write(chunk)
        try:
            replace_with_retry(tmp, dest)
        except PermissionError:
            if not dest.exists():  # the other download holds it: theirs is the same file
                raise
    finally:
        tmp.unlink(missing_ok=True)
    return dest
