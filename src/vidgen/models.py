"""Data contracts shared across pipeline stages (persisted as JSON in output/<slug>/)."""

from typing import Literal

from pydantic import BaseModel, Field

VisualType = Literal["stock", "ai_image", "ai_video"]
AssetKind = Literal["video", "image", "color"]  # color = placeholder when nothing was found


class Scene(BaseModel):
    id: int
    narration: str
    visual_query: str = Field(description="2-4 concrete English nouns for stock search")
    alt_queries: list[str] = []  # other ways to film the same scene (close-up, wider setting)
    visual_type: VisualType = "stock"
    ai_prompt: str = ""
    chapter: str | None = None


class SourceRef(BaseModel):
    title: str
    url: str


class SourceDoc(SourceRef):
    """A reference article with the passages selected for the script prompt."""
    lang: str
    text: str


AngleStyle = Literal["explain", "myth", "story"]


class Angle(BaseModel):
    """One way to tell the topic, proposed in the brief step and editable by the user."""
    style: AngleStyle
    title: str
    hook: str
    key_points: list[str]
    keywords: list[str] = []
    sources: list[SourceDoc] = []  # researched once per topic, shared by all angles


class Brief(BaseModel):
    topic: str
    angles: list[Angle] = Field(min_length=1)
    chosen: int | None = None
    excluded_urls: list[str] = []  # sources the user unticked
    # the angle (with only its ticked sources) the CURRENT script was written from; kept when new
    # angles are generated so fact-check / extend / rewrite keep their facts until another is chosen
    script_angle: Angle | None = None

    def chosen_angle(self) -> Angle | None:
        if self.chosen is not None:
            return self.angles[self.chosen]
        return self.script_angle

    def chosen_sources(self) -> list[SourceDoc]:
        if self.chosen is not None:
            return [s for s in self.angles[self.chosen].sources if s.url not in self.excluded_urls]
        return list(self.script_angle.sources) if self.script_angle else []


class Script(BaseModel):
    title: str
    hook: str
    lang: Literal["vi", "en"]
    format: Literal["short", "long"]
    scenes: list[Scene] = Field(min_length=1)
    sources: list[SourceRef] = []  # reference pages the facts were drawn from
    mood: str = ""  # background music mood (assemble/music.py MOODS); "" = any track

    @property
    def word_count(self) -> int:
        return sum(len(s.narration.split()) for s in self.scenes)


class WordTiming(BaseModel):
    word: str
    start: float
    end: float


class SceneAudio(BaseModel):
    scene_id: int
    path: str
    start: float  # global offset in final timeline
    duration: float
    words: list[WordTiming]  # global times


class Timeline(BaseModel):
    scenes: list[SceneAudio] = Field(min_length=1)

    @property
    def duration(self) -> float:
        last = self.scenes[-1]
        return last.start + last.duration


class Alternate(BaseModel):
    """A ranked stock candidate that was not used, kept so a scene's clip can be swapped later."""
    uid: str
    kind: AssetKind
    download_url: str
    page_url: str
    width: int
    height: int
    duration: float
    author: str
    source: str
    license: str
    text: str = ""  # clip description, for relevance when swapping
    thumb: str = ""  # preview image, for the vision judge


class Asset(BaseModel):
    scene_id: int
    path: str  # relative to the video's output dir; empty for color placeholders
    kind: AssetKind
    uid: str = ""      # stock candidate id ("pexels:v123"), used to avoid duplicates across scenes
    query: str = ""    # search that found it
    alternates: list[Alternate] = []
    rejected: list[str] = []  # clips the user swapped away from this scene (uid or page url)
    vision_score: int | None = None  # 0-10 from the vision judge; None = not judged
    extra: list[str] = []     # second clip(s) for long scenes, cut in as another shot
    extra_uids: list[str] = []  # their stock ids: a swap elsewhere must not reuse them

    @property
    def ident(self) -> str:
        """uid when known; older assets.json files only have the page url."""
        return self.uid or self.url
    source: str  # pexels | pixabay | flux | wan | placeholder
    url: str = ""
    author: str = ""
    license: str = ""
