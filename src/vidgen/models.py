"""Data contracts shared across pipeline stages (persisted as JSON in output/<slug>/)."""

from typing import Literal

from pydantic import BaseModel, Field

VisualType = Literal["stock", "ai_image", "ai_video"]
AssetKind = Literal["video", "image", "color"]  # color = placeholder when nothing was found


class Scene(BaseModel):
    id: int
    narration: str
    visual_query: str = Field(description="2-4 concrete English nouns for stock search")
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

    def chosen_angle(self) -> Angle | None:
        return self.angles[self.chosen] if self.chosen is not None else None

    def chosen_sources(self) -> list[SourceDoc]:
        angle = self.chosen_angle()
        return [s for s in angle.sources if s.url not in self.excluded_urls] if angle else []


class Script(BaseModel):
    title: str
    hook: str
    lang: Literal["vi", "en"]
    format: Literal["short", "long"]
    scenes: list[Scene] = Field(min_length=1)
    sources: list[SourceRef] = []  # reference pages the facts were drawn from

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


class Asset(BaseModel):
    scene_id: int
    path: str  # relative to the video's output dir; empty for color placeholders
    kind: AssetKind
    source: str  # pexels | pixabay | flux | wan | placeholder
    url: str = ""
    author: str = ""
    license: str = ""
