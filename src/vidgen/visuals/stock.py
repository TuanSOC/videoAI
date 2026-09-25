"""Pexels + Pixabay search → normalized candidates. Search responses are cached on disk to spare API quota
(Pexels: 200 req/hour). Both licenses allow free commercial use without attribution; we credit anyway."""

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger(__name__)
PER_PAGE = 15
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


class StockClient:
    source = ""
    license = ""

    def __init__(self, api_key: str, cache_dir: Path, http: httpx.Client | None = None):
        self.api_key = api_key
        self.cache_dir = cache_dir / "search"
        self.http = http or httpx.Client(timeout=30, follow_redirects=True)

    def _get(self, url: str, params: dict, headers: dict | None = None) -> dict:
        key = hashlib.sha1(json.dumps([self.source, url, params], sort_keys=True).encode()).hexdigest()
        cached = self.cache_dir / f"{key}.json"
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))
        for attempt in range(2):
            resp = self.http.get(url, params=params, headers=headers)
            if resp.status_code == 429 and attempt == 0:
                wait = min(int(resp.headers.get("Retry-After", "30")), 60)
                log.warning("%s rate limited, waiting %ss", self.source, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        data = resp.json()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(data), encoding="utf-8")
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
        out = []
        for v in data.get("videos", []):
            f = pick_file([x for x in v.get("video_files", []) if x.get("file_type") == "video/mp4"])
            if f:
                out.append(Candidate(f"pexels:v{v['id']}", "video", f["link"], v.get("url", ""),
                                     f["width"] or v["width"], f["height"] or v["height"],
                                     float(v.get("duration") or 0), v.get("user", {}).get("name", ""),
                                     self.source, self.license))
        return out

    def photos(self, query, orientation):
        data = self._get("https://api.pexels.com/v1/search",
                         {"query": query, "orientation": orientation, "per_page": PER_PAGE},
                         self._headers())
        return [Candidate(f"pexels:p{p['id']}", "image", p["src"]["large2x"], p.get("url", ""),
                          p["width"], p["height"], 0.0, p.get("photographer", ""), self.source, self.license)
                for p in data.get("photos", [])]


class Pixabay(StockClient):
    source = "pixabay"
    license = "Pixabay Content License"

    def videos(self, query, orientation):
        data = self._get("https://pixabay.com/api/videos/",
                         {"key": self.api_key, "q": query[:100], "per_page": PER_PAGE, "safesearch": "true"})
        out = []
        for h in data.get("hits", []):
            f = pick_file(list(h.get("videos", {}).values()))
            if f:
                out.append(Candidate(f"pixabay:v{h['id']}", "video", f["url"], h.get("pageURL", ""),
                                     f["width"], f["height"], float(h.get("duration") or 0),
                                     h.get("user", ""), self.source, self.license))
        return out

    def photos(self, query, orientation):
        data = self._get("https://pixabay.com/api/",
                         {"key": self.api_key, "q": query[:100], "per_page": PER_PAGE, "image_type": "photo",
                          "orientation": "vertical" if orientation == "portrait" else "horizontal",
                          "safesearch": "true"})
        return [Candidate(f"pixabay:p{h['id']}", "image", h["largeImageURL"], h.get("pageURL", ""),
                          h.get("imageWidth", 0), h.get("imageHeight", 0), 0.0, h.get("user", ""),
                          self.source, self.license)
                for h in data.get("hits", [])]


def download(url: str, cache_dir: Path, http: httpx.Client) -> Path:
    """Download once into cache/files/, keyed by URL."""
    suffix = ".mp4" if ".mp4" in url.split("?")[0] else Path(url.split("?")[0]).suffix or ".jpg"
    dest = cache_dir / "files" / (hashlib.sha1(url.encode()).hexdigest() + suffix)
    if dest.exists():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with http.stream("GET", url) as resp:
        resp.raise_for_status()
        with tmp.open("wb") as fh:
            for chunk in resp.iter_bytes(1 << 16):
                fh.write(chunk)
    _replace_with_retry(tmp, dest)
    return dest


def _replace_with_retry(src: Path, dest: Path, attempts: int = 8) -> None:
    """Windows antivirus briefly locks freshly written files (WinError 32); wait it out."""
    for attempt in range(attempts):
        try:
            src.replace(dest)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(0.1 * 2 ** attempt)  # 0.1s … 12.8s total
