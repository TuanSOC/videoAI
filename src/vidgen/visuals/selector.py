"""Per-scene visual sourcing with a fallback chain.

  ai_video scene : Wan (budgeted) → Flux still → stock chain
  ai_image scene : Flux → stock chain
  stock chain    : stock video → stock photo → Flux → color placeholder
"""

import hashlib
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
EXTRA_AFTER = 5.0  # seconds: longer scenes also get their next-best clip, cut in as a second shot
VISION_TOP = 5     # thumbnails shown to the vision judge per search round
MIN_VISION = 5     # below this the clip only illustrates loosely: keep looking
VISION_CALLS_PER_SCENE = 2
IMAGE_PENALTY = 1  # a still image scores one point below an equally good video


class SwapError(RuntimeError):
    pass


def _alternate(c: Candidate | Alternate) -> Alternate:
    if isinstance(c, Alternate):
        return c
    return Alternate(uid=c.uid, kind=c.kind, download_url=c.download_url, page_url=c.page_url,
                     width=c.width, height=c.height, duration=c.duration, author=c.author,
                     source=c.source, license=c.license, text=c.text, thumb=c.thumb)


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
    """Idempotent singular form used as a match key: octopuses→octopus, buses→bus, houses→house,
    caves→cave, boxes→box, bodies→body. No trailing-"e" stripping: it merged car/care, plan/plane."""
    if len(w) <= 3:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("es"):
        base = w[:-2]
        # "-uses": a consonant before "us" means the singular ends in -us (octop-us, b-us, vir-us);
        # a vowel means it ends in -use (ho-use, ca-use, pa-use)
        if base.endswith(("ss", "x", "z", "ch", "sh")) or (
                base.endswith("us") and len(base) >= 3 and base[-3] not in "aeiou"):
            return base
        return w[:-1]
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


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
    dj dancing wedding keychain keyring jewelry jewellery necklace earring pendant tattoo mug shirt
    print poster""".split()}


# Never usable whatever the query: a green screen shows nothing, mockups are templates to fill in.
JUNK = ("green screen", "greenscreen", "chroma key", "chromakey", "mockup", "mock up", "template")


def is_junk(c: Candidate | Alternate) -> bool:
    text = c.text.lower().replace("-", " ")
    return any(j in text for j in JUNK)


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
        # search with the words as written, punctuation stripped ("caves." → "caves"); the subject
        # may sit after a comma. stem() yields real singular words, so it is a safe last resort.
        tokens = [t.lower() for t in re.findall(r"[A-Za-z]+", query)]
        first_tokens = [t.lower() for t in re.findall(r"[A-Za-z]+", parts[0])]
        subject_word = next((t for t in tokens if stem(t) == subject), subject)
        others = content_words(parts[0]) - {subject}
        last = next((t for t in reversed(first_tokens) if stem(t) in others), None)
        if last:
            out.append(f"{subject_word} {last}")
        out.append(subject_word)
    else:  # no shared subject: two-word phrases of real words ("email with" is no query), a lone last
        # word ("activity") is too vague
        words = [t for t in words if t.lower().strip(".") not in STOPWORDS]
        if len(words) > 2:
            out.append(" ".join(words[:2]))
            out.append(" ".join(words[-2:]))
        elif len(words) == 2:
            out.append(" ".join(words) if len(parts[0].split()) > 2 else words[-1])
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
                 judge: Judge | None = None, vision=None):
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
        self.vision = vision    # optional vision judge (visuals/vision.py): scores thumbnails
        self.vision_calls = 0
        self.pool: ThreadPoolExecutor | None = None  # set while sourcing a whole video
        self.pending: list[tuple[Scene, Asset, Future]] = []
        self.extra_pending: list[tuple[Asset, str, Future]] = []

    def pick(self, scene: Scene, seconds: float) -> Asset:
        self.visuals_dir.mkdir(parents=True, exist_ok=True)
        self.vision_calls = 0
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
        queries = [query_text] if query_text else [scene.visual_query, *scene.alt_queries]
        base = queries[0]
        subject = self.subject if self.subject in content_words(base) else None
        # first all of the scene's queries together (the same moment filmed differently), then shorter
        # forms of the main one: stock search is literal and misses on long phrases
        rounds = [queries] + [[q] for q in fallback_queries(base, subject)[1:]]
        judged = False  # one LLM judgement per search: ~16 calls for one scene were possible
        fallback: tuple[int, Candidate, str, int] | None = None  # best poorly-scored clip seen
        for round_queries in rounds:
            found: dict[str, tuple[Candidate, str]] = {}
            for query in round_queries:
                for client in self.clients:
                    try:
                        hits = client.videos(query, self.orientation) if kind == "video" \
                            else client.photos(query, self.orientation)
                    except httpx.HTTPError as e:
                        log.warning("%s search failed for %r: %s", client.source, query, e)
                        continue
                    for c in hits:
                        found.setdefault(c.uid, (c, query))
            # compare page urls too: assets saved before uids existed only carry the url
            fresh = [(c, q) for c, q in found.values()
                     if c.uid not in self.used and c.page_url not in self.used and not is_junk(c)]

            def rel(c: Candidate, q: str) -> int:  # against the query that found it
                words = content_words(q)
                return relevance(c, words, self.subject if self.subject in words else None)

            rated = sorted(((rel(c, q), score(c, self.orientation, seconds), c, q) for c, q in fresh),
                           key=lambda t: (t[0], t[1]), reverse=True)
            if strict:
                rated = [t for t in rated if t[0] >= 1]
            ranked = [c for _, _, c, _ in rated]
            query_of = {c.uid: q for _, _, c, q in rated}
            if not ranked:
                continue
            seen = self._vision_scores(scene, queries, ranked)
            raw: dict[str, int] = {}
            if seen is not None:
                judged = True  # the vision model has looked at them: no text tie-breaker needed
                raw, adjusted = seen
                good = sorted((c for c in ranked if adjusted.get(c.uid, -1) >= MIN_VISION),
                              key=lambda c: -adjusted[c.uid])
                if not good:
                    best_low = max((c for c in ranked if c.uid in adjusted), key=lambda c: adjusted[c.uid])
                    if fallback is None or adjusted[best_low.uid] > fallback[0]:
                        fallback = (adjusted[best_low.uid], best_low, query_of[best_low.uid], raw[best_low.uid])
                    if strict:
                        continue  # nothing good in this round: a simpler query may find better
                    good = [best_low]
                # clips the model scored low are neither used nor offered as swaps
                ranked = good + [c for c in ranked if c.uid not in adjusted]
            # weak match (the best clip misses several query words, e.g. "man eating pizza at his computer
            # screen" for "computer screen showing malware"): let the LLM read the narration and choose
            best_words = content_words(rated[0][3])
            matched = rated[0][0] - (1 if self.subject in best_words else 0)
            weak = matched < max(2, len(best_words) - 1)
            if self.judge and weak and len(ranked) > 1 and not judged:
                judged = True
                top = ranked[:JUDGE_TOP]
                idx = self.judge(scene.narration, base, [c.text for c in top])
                if idx == -1 and strict:
                    continue  # the LLM rejected all of them: a simpler query may find better
                if idx is not None and 0 <= idx < len(top):
                    ranked.insert(0, ranked.pop(idx))
            while ranked:
                best, ranked = ranked[0], ranked[1:]
                # whole-video sourcing only: a swap replaces the main clip and drops any extra
                long_scene = self.pool is not None and seconds > EXTRA_AFTER
                extra = next((c for c in ranked if c.kind == kind), None) if long_scene else None
                rest = [c for c in ranked if c is not extra]
                asset = self._use(scene, best, query_of[best.uid], rest[:MAX_ALTERNATES])
                if asset and extra is not None:
                    self._use_extra(scene, asset, extra)
                if asset:
                    asset.vision_score = raw.get(best.uid)
                    return asset
        if fallback is not None:  # only weak matches anywhere: the best of them beats a placeholder
            _, c, query, raw_score = fallback
            asset = self._use(scene, c, query, [])
            if asset:
                asset.vision_score = raw_score
            return asset
        return None

    def _vision_scores(self, scene: Scene, queries: list[str],
                       ranked: list[Candidate]) -> tuple[dict[str, int], dict[str, int]] | None:
        """(raw, adjusted) vision scores by uid for the top thumbnails, or None (no judge / no opinion)."""
        if self.vision is None or self.vision_calls >= VISION_CALLS_PER_SCENE:
            return None
        shown, images = [], []
        for c in ranked:
            if len(shown) == VISION_TOP:
                break
            image = self._thumb(c.thumb) if c.thumb else None
            if image:
                shown.append(c)
                images.append(image)
        if not shown:
            return None
        self.vision_calls += 1
        scores = self.vision.score(scene.narration, queries, images)
        if scores is None:
            return None
        raw = {c.uid: s for c, s in zip(shown, scores)}
        adjusted = {c.uid: s - (IMAGE_PENALTY if c.kind == "image" else 0) for c, s in zip(shown, scores)}
        log.info("scene %d: vision scores %s", scene.id, raw)
        return raw, adjusted

    def _thumb(self, url: str) -> bytes | None:
        """Thumbnail bytes, cached on disk (re-sourcing a video costs no downloads)."""
        path = self.cache_dir / "thumbs" / f"{hashlib.sha1(url.encode()).hexdigest()}.jpg"
        if path.exists():
            return path.read_bytes()
        try:
            resp = self.http.get(url, timeout=20)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            log.debug("thumbnail failed %s: %s", url, e)
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(resp.content)
        return resp.content

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

    def _use_extra(self, scene: Scene, asset: Asset, c: Candidate) -> None:
        self.used.add(c.uid)
        dest = self.visuals_dir / f"scene_{scene.id:03d}b{url_suffix(c.download_url)}"
        rel = f"visuals/{dest.name}"
        asset.extra.append(rel)
        self.extra_pending.append((asset, rel, self.pool.submit(self._fetch, c.download_url, dest)))

    def _fetch(self, url: str, dest: Path) -> None:
        _link_or_copy(download(url, self.cache_dir, self.http), dest)

    def finish(self, assets: list[Asset]) -> list[Asset]:
        """Wait for background downloads; a failed one falls back to that scene's alternates
        (downloaded synchronously), then to a placeholder."""
        pending, self.pending = self.pending, []
        extras, self.extra_pending = self.extra_pending, []
        for asset, rel, future in extras:
            try:
                future.result()
            except Exception as e:  # an extra shot is optional: just drop it
                log.warning("scene %d: extra clip download failed (%s)", asset.scene_id, e)
                asset.extra.remove(rel)
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
        self.vision_calls = 0
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
    vision = vision_judge(s)
    # the vision judge replaces the text judge: switching models on 8 GB of VRAM for every scene is slow
    return Selector(clients, ai, preset, out_dir, cache, subject=subject,
                    judge=None if vision else llm_judge(s), vision=vision)


def vision_judge(s: Settings):
    from vidgen.visuals.vision import VisionJudge

    return VisionJudge(s.secrets.ollama_url, s.pipeline.vision.model) if s.pipeline.vision.enabled else None


def vision_session(selector: Selector, s: Settings):
    """Context: free the script model's VRAM before the vision model loads, and the vision model after."""
    from contextlib import contextmanager

    from vidgen.visuals.vision import unload

    @contextmanager
    def session():
        if selector.vision is not None:
            unload(s.secrets.ollama_url, s.pipeline.llm.ollama_model)
        try:
            yield
        finally:
            if selector.vision is not None:
                unload(s.secrets.ollama_url, s.pipeline.vision.model)

    return session()


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
        with vision_session(selector, s), ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
            selector.pool = pool
            assets = [selector.pick(scene, seconds.get(scene.id, 5.0)) for scene in script.scenes]
            return selector.finish(assets)
    finally:
        selector.pool = None
        if selector.ai is not None:
            selector.ai.client.free()
