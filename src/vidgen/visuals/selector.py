"""Per-scene visual sourcing with a fallback chain.

  ai_video scene : Wan (budgeted) → Flux still → stock chain
  ai_image scene : Flux → stock chain
  stock chain    : stock video → stock photo → Flux → color placeholder
"""

import logging
import os
import re
import shutil
from collections import Counter
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import httpx
from pydantic import BaseModel

from vidgen.config import FormatPreset, Settings
from vidgen.models import Alternate, Asset, Scene, Script, Timeline
from vidgen.visuals.stock import Candidate, Pexels, Pixabay, StockClient, download, url_suffix

log = logging.getLogger(__name__)
MIN_SHORT_SIDE = 720


MAX_ALTERNATES = 3
DOWNLOAD_WORKERS = 4
MAX_REJECTED = 50


class SwapError(RuntimeError):
    pass


def _alternate(c: Candidate | Alternate) -> Alternate:
    if isinstance(c, Alternate):
        return c
    return Alternate(uid=c.uid, kind=c.kind, download_url=c.download_url, page_url=c.page_url,
                     width=c.width, height=c.height, duration=c.duration, author=c.author,
                     source=c.source, license=c.license, text=c.text)


def score(c: Candidate, orientation: str, scene_seconds: float) -> int:
    """Technical fit only (framing, resolution, length); relevance is ranked first, see relevance()."""
    s = 3 if (c.height > c.width) == (orientation == "portrait") else 0
    s += 2 if min(c.width, c.height) >= MIN_SHORT_SIDE else 0
    if c.kind == "video":
        s += 2 if c.duration >= scene_seconds else 0
    return s


STOPWORDS = set("""a an the of in on at to from for with and or by over under between into onto near
    through during about above below up down out off while being is are was were be showing shows show
    view views shot shots footage video clip close closeup background image photo picture scene""".split())
SUBJECT_SHARE = 0.4  # a word in ≥40% of the scenes' queries is what the video is about


def stem(w: str) -> str:
    """Crude, idempotent plural folding (caves→cave, octopuses→octopus, octopus stays)."""
    if w.endswith("uses") and len(w) > 5:
        return w[:-2]
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("us", "ss", "is")) or len(w) <= 3:
        return w
    return w[:-1] if w.endswith("s") else w


def content_words(text: str) -> set[str]:
    return {stem(w) for w in re.findall(r"[a-z]+", text.lower()) if w not in STOPWORDS and len(w) > 1}


def video_subject(queries: list[str]) -> str | None:
    """The word most scenes' queries share ("octopus"), if any is shared by enough of them."""
    counts: Counter[str] = Counter(w for q in queries for w in content_words(q))
    if not counts:
        return None
    word, n = counts.most_common(1)[0]
    return word if n >= max(2, SUBJECT_SHARE * len(queries)) else None


# Descriptions that show the subject out of context (a plated octopus, an octopus kite, a statue).
# Penalised unless the scene's own query asks for them. Found on real picks, not guessed.
OFF_CONTEXT = {stem(w) for w in """dish dishes food meal plate cooking cooked cook grilled fried recipe
    restaurant menu seafood market sushi sculpture statue toy toys kite kites cartoon illustration drawing
    painting logo icon animation animated case costume plush sticker helmet eating pizza drinking party
    dj dancing wedding""".split()}


def relevance(c: Candidate, query_words: set[str], subject: str | None) -> int:
    """Query words found in the clip's description; the subject counts double and, when the scene is
    about it, is mandatory (0 = irrelevant). Off-context words (food, toys, statues…) cost 2 unless the
    query itself asks for them. Clips without any description get a neutral 1."""
    text_words = content_words(c.text)
    if not text_words:
        return 1
    if subject and subject not in text_words:
        return 0
    off = len((text_words & OFF_CONTEXT) - query_words)
    return max(0, len(query_words & text_words) + (1 if subject else 0) - 2 * off)


def fallback_queries(query: str, subject: str | None = None) -> list[str]:
    """Each comma-separated idea on its own (LLMs often list several), then shorter forms of the first:
    stock search is literal and misses on long phrases. Shorter forms keep the subject
    ("octopus moving between caves" → "octopus cave" → "octopus", never just "caves")."""
    parts = [p.strip() for p in re.split(r"[,;]", query) if p.strip()] or [query]
    out = list(parts)
    words = parts[0].split()
    if subject:
        others = [w for w in content_words(parts[0]) - {subject}]
        last = next((stem(w.lower()) for w in reversed(words) if stem(w.lower()) in others), None)
        if last:
            out.append(f"{subject} {last}")
        out.append(subject)
    else:  # no shared subject: keep two-word phrases, a lone last word ("activity") is too vague
        if len(words) > 2:
            out.append(" ".join(words[:2]))
            out.append(" ".join(words[-2:]))
        elif len(words) == 2:
            out.append(words[-1])
    return list(dict.fromkeys(out))


def _link_or_copy(src: Path, dest: Path) -> None:
    dest.unlink(missing_ok=True)
    try:
        os.link(src, dest)  # same drive: no extra disk space for 100-scene videos
    except OSError:
        shutil.copy2(src, dest)


Judge = Callable[[str, str, list[str]], int | None]  # → index, -1 none fits, None no opinion
JUDGE_TOP = 5


class Selector:
    def __init__(self, clients: list[StockClient], ai, preset: FormatPreset, out_dir: Path,
                 cache_dir: Path, http: httpx.Client | None = None, subject: str | None = None,
                 judge: Judge | None = None):
        self.clients = clients
        self.ai = ai  # AIGenerator or None when ComfyUI is offline
        self.orientation = preset.orientation
        self.ai_video_budget = preset.max_ai_video
        self.visuals_dir = out_dir / "visuals"
        self.cache_dir = cache_dir
        self.http = http or httpx.Client(timeout=120, follow_redirects=True)
        self.used: set[str] = set()
        self.subject = subject  # what the whole video is about, see video_subject()
        self.judge = judge      # optional LLM tie-breaker for weak matches
        self.pool: ThreadPoolExecutor | None = None  # set while sourcing a whole video
        self.pending: list[tuple[Scene, Asset, Future]] = []

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
        # nothing relevant anywhere: the closest stock clip still beats a flat colour
        steps += [lambda: self._stock(scene, seconds, "video", strict=False),
                  lambda: self._stock(scene, seconds, "image", strict=False)]
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

    def _stock(self, scene: Scene, seconds: float, kind: str, query_text: str | None = None,
               strict: bool = True) -> Asset | None:
        """Search stock for the scene. Ranking: relevance to the scene query first, technical fit second.
        strict: skip clips whose description doesn't match (a fallback query or the next step may do
        better); non-strict is the last resort before a placeholder."""
        base = query_text or scene.visual_query
        query_words = content_words(base)
        subject = self.subject if self.subject in query_words else None
        for query in fallback_queries(base, subject):
            candidates: list[Candidate] = []
            for client in self.clients:
                try:
                    candidates += client.videos(query, self.orientation) if kind == "video" \
                        else client.photos(query, self.orientation)
                except httpx.HTTPError as e:
                    log.warning("%s search failed for %r: %s", client.source, query, e)
            # compare page urls too: assets saved before uids existed only carry the url
            fresh = [c for c in candidates if c.uid not in self.used and c.page_url not in self.used]
            rated = sorted(((relevance(c, query_words, subject), score(c, self.orientation, seconds), c)
                            for c in fresh), key=lambda t: (t[0], t[1]), reverse=True)
            if strict:
                rated = [t for t in rated if t[0] >= 1]
            ranked = [c for _, _, c in rated]
            if not ranked:
                continue
            # weak match (the best clip misses several query words, e.g. "man eating pizza at his computer
            # screen" for "computer screen showing malware"): let the LLM read the narration and choose
            matched = rated[0][0] - (1 if subject else 0)
            weak = matched < max(2, len(query_words) - 1)
            if self.judge and weak and len(ranked) > 1:
                top = ranked[:JUDGE_TOP]
                idx = self.judge(scene.narration, base, [c.text for c in top])
                if idx == -1 and strict:
                    continue  # the LLM rejected all of them: a simpler query may find better
                if idx is not None and 0 <= idx < len(top):
                    ranked.insert(0, ranked.pop(idx))
            while ranked:
                best, ranked = ranked[0], ranked[1:]
                asset = self._use(scene, best, query, ranked[:MAX_ALTERNATES])
                if asset:
                    return asset
        return None

    def _use(self, scene: Scene, c: Candidate | Alternate, query: str,
             alternates: list[Candidate | Alternate]) -> Asset | None:
        """Download a candidate into visuals/scene_NNN.ext; keep the next-best ones for later swaps.
        With a download pool (whole-video sourcing) the download runs in the background and failures
        are repaired in finish(); without one (a single swap) it happens right here."""
        self.used.add(c.uid)
        dest = self.visuals_dir / f"scene_{scene.id:03d}{url_suffix(c.download_url)}"
        asset = Asset(scene_id=scene.id, path=f"visuals/{dest.name}", kind=c.kind, source=c.source,
                      url=c.page_url, author=c.author, license=c.license, uid=c.uid, query=query,
                      alternates=[_alternate(a) for a in alternates])
        if self.pool is not None:
            self.pending.append((scene, asset, self.pool.submit(self._fetch, c.download_url, dest)))
            return asset
        try:
            self._fetch(c.download_url, dest)
        except httpx.HTTPError as e:
            log.warning("download failed %s: %s", c.download_url, e)
            return None
        return asset

    def _fetch(self, url: str, dest: Path) -> None:
        _link_or_copy(download(url, self.cache_dir, self.http), dest)

    def finish(self, assets: list[Asset]) -> list[Asset]:
        """Wait for background downloads; a failed one falls back to that scene's alternates
        (downloaded synchronously), then to a placeholder."""
        pending, self.pending = self.pending, []
        failed: dict[int, tuple[Scene, Asset]] = {}
        for scene, asset, future in pending:
            try:
                future.result()
            except Exception as e:  # network errors, disk errors
                log.warning("scene %d: download failed (%s), trying alternates", scene.id, e)
                failed[scene.id] = (scene, asset)
        if not failed:
            return assets
        pool, self.pool = self.pool, None
        try:
            fixed = {}
            for sid, (scene, asset) in failed.items():
                repl = None
                alts = list(asset.alternates)
                while alts and repl is None:
                    alt, alts = alts[0], alts[1:]
                    if alt.uid not in self.used:
                        repl = self._use(scene, alt, asset.query, alts)
                fixed[sid] = repl or Asset(scene_id=sid, path="", kind="color", source="placeholder")
            return [fixed.get(a.scene_id, a) for a in assets]
        finally:
            self.pool = pool

    def swap(self, scene: Scene, seconds: float, current: Asset, used: set[str],
             query: str | None = None) -> Asset:
        """Another clip for one scene, never one already used anywhere in the video.
        No query: next saved alternate, else a fresh search on the scene's query. Query: search it."""
        self.visuals_dir.mkdir(parents=True, exist_ok=True)
        # never offer back the current clip nor any clip already swapped away from this scene
        self.used = set(used) | {current.uid, current.url, *current.rejected} - {""}
        rejected = (current.rejected + [current.ident])[-MAX_REJECTED:] if current.ident else current.rejected
        asset = None
        if not query:
            pending = [a for a in current.alternates if a.uid not in self.used and a.page_url not in self.used]
            while pending and asset is None:
                alt, pending = pending[0], pending[1:]
                asset = self._use(scene, alt, current.query or scene.visual_query, pending)
        for strict in (True, False):
            for kind in ("video", "image"):
                if asset is None:
                    asset = self._stock(scene, seconds, kind, query or current.query or scene.visual_query,
                                        strict=strict)
        if asset is None:
            raise SwapError("Không tìm được clip khác — thử từ khóa khác")
        return asset.model_copy(update={"rejected": rejected})


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
    subject = video_subject([sc.visual_query for sc in script.scenes])
    log.info("video subject for clip matching: %s", subject)
    return Selector(clients, ai, preset, out_dir, cache, subject=subject, judge=llm_judge(s))


class ClipPick(BaseModel):
    index: int  # -1 = none fits


JUDGE_PROMPT = """A video scene is narrated as: "{narration}"
Stock search: "{query}"
Candidate clips (descriptions):
{listing}

Pick the clip that best illustrates the narration's key concept. Reject clips whose main action is
unrelated to it (someone cooking an animal when the scene is about the living animal; a person eating,
partying or DJing when the scene is about cyber attacks). Answer index -1 if none fits."""


def llm_judge(s: Settings) -> Judge | None:
    """LLM tie-breaker for weak matches. Failures just mean "no opinion" (keep the heuristic order)."""
    from vidgen.script.llm import default_chain

    try:
        llm = default_chain(s)
    except Exception:
        return None

    def judge(narration: str, query: str, texts: list[str]) -> int | None:
        listing = "\n".join(f"{i}. {t or '(no description)'}" for i, t in enumerate(texts))
        try:
            pick = llm.generate(JUDGE_PROMPT.format(narration=narration, query=query, listing=listing), ClipPick)
        except Exception as e:
            log.warning("clip judge failed: %s", e)
            return None  # no opinion: keep the heuristic order
        return pick.index if pick.index >= 0 else -1  # -1: none fits

    return judge


def source_visuals(script: Script, timeline: Timeline, preset: FormatPreset, out_dir: Path,
                   s: Settings, selector: Selector | None = None) -> list[Asset]:
    selector = selector or build_selector(script, preset, out_dir, s)
    seconds = {sa.scene_id: sa.duration for sa in timeline.scenes}
    try:
        # choose sequentially (cheap, cached searches; keeps clips unique), download in parallel
        with ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
            selector.pool = pool
            assets = [selector.pick(scene, seconds.get(scene.id, 5.0)) for scene in script.scenes]
            return selector.finish(assets)
    finally:
        selector.pool = None
        if selector.ai is not None:
            selector.ai.client.free()
