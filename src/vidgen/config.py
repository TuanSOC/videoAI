"""Settings: secrets from .env, pipeline options from config.yaml."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

Format = Literal["short", "long"]
Lang = Literal["vi", "en"]


LANG_NAMES = {"vi": "Vietnamese", "en": "English"}  # for prompts


class FormatPreset(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in config.yaml is an error, not ignored
    width: int
    height: int
    fps: int
    target_seconds: tuple[int, int]
    max_ai_video: int

    @property
    def orientation(self) -> str:
        return "portrait" if self.height > self.width else "landscape"


LLM_PROVIDERS = ("groq", "ollama", "gemini")


class LLMConfig(BaseModel):
    """Provider chains per role, tried in order. A spec is "name" or "name:model" ("groq:openai/gpt-oss-120b",
    "ollama:qwen3:8b"); a bare "ollama"/"gemini" uses ollama_model/gemini_model."""
    model_config = ConfigDict(extra="forbid")  # a typo in config.yaml is an error, not ignored
    creative: list[str] = ["ollama"]   # briefs, scripts, rewrites, hooks, metadata, topic ideas
    checker: list[str] = ["ollama"]    # fact-check
    # a second, different model checks again and the flags are merged: one run misses what another catches
    # (seen live: gpt-oss-120b let an invented year through; its answers also vary run to run). [] = one pass
    second_checker: list[str] = []
    judge: list[str] = ["ollama"]      # clip tie-breaks: many small calls per video, kept local
    ollama_model: str = "qwen3:8b"
    ollama_think: bool = False
    gemini_model: str = "gemini-2.5-flash"

    @field_validator("creative", "checker", "second_checker", "judge")
    @classmethod
    def _specs(cls, specs: list[str]) -> list[str]:
        for spec in specs:
            name, _, model = spec.partition(":")
            if name not in LLM_PROVIDERS:
                raise ValueError(f"unknown LLM provider {spec!r} (one of {', '.join(LLM_PROVIDERS)})")
            if name == "groq" and not model:
                raise ValueError("groq needs a model: groq:<model>")
        return specs

    def uses(self, name: str) -> bool:
        return any(spec.partition(":")[0] == name
                   for spec in self.creative + self.checker + self.second_checker + self.judge)


class VisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in config.yaml is an error, not ignored
    enabled: bool = True           # score stock thumbnails with a local vision model before picking
    model: str = "qwen2.5vl:7b"    # "qwen2.5vl:3b" is faster and lighter, less accurate
    # with an AI image generator available, a stock clip must score this (0-10) or the scene is drawn from its
    # shot note instead; a lower clip comes back only if drawing fails. 0 = off. No generator: no effect.
    ai_bar: int = 7


class SfxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in config.yaml is an error, not ignored
    enabled: bool = True
    density: Literal["minimal", "subtle", "dense"] = "subtle"


class DocumentsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True  # figure scenes shown as the highlighted source sentence (visuals/document.py)


class WhisperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in config.yaml is an error, not ignored
    model: str = "small"
    device: str = "cuda"
    compute_type: str = "float16"


class AIConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in config.yaml is an error, not ignored
    image_size: dict[str, tuple[int, int]] = {"short": (768, 1344), "long": (1344, 768)}
    video_size: dict[str, tuple[int, int]] = {"short": (544, 960), "long": (960, 544)}
    video_frames: int = 81
    timeout_seconds: int = 1800
    # stills: the first usable source draws (comfyui = local, when it answers; cloudflare = Workers AI free
    # daily allocation, needs CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN). AI video is ComfyUI only.
    image_sources: list[Literal["comfyui", "cloudflare"]] = ["comfyui", "cloudflare"]
    cloudflare_model: str = "@cf/black-forest-labs/flux-2-klein-4b"   # 9:16 frames; flux-1-schnell = square


class PipelineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in config.yaml is an error, not ignored
    output_dir: Path = Path("output")
    cache_dir: Path = Path("cache")
    formats: dict[str, FormatPreset]
    voices: dict[str, str]
    # edge-tts speaking rate ("+8%", "-5%"); the default pace sounds slow for short videos
    voice_rate: str = Field("+8%", pattern=r"^[+-]\d{1,2}%$")
    llm: LLMConfig = LLMConfig()
    vision: VisionConfig = VisionConfig()
    sfx: SfxConfig = SfxConfig()
    documents: DocumentsConfig = DocumentsConfig()
    research: bool = True  # ground scripts in Wikipedia passages (script/research.py)
    ai: AIConfig = AIConfig()
    whisper: WhisperConfig = WhisperConfig()


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    gemini_api_key: str = ""
    groq_api_key: str = ""
    cloudflare_account_id: str = ""
    cloudflare_api_token: str = ""
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    comfyui_url: str = "http://127.0.0.1:8188"
    ollama_url: str = "http://127.0.0.1:11434"


class Settings(BaseModel):
    pipeline: PipelineConfig
    secrets: Secrets

    def preset(self, fmt: Format) -> FormatPreset:
        return self.pipeline.formats[fmt]

    def path(self, p: Path) -> Path:
        return p if p.is_absolute() else ROOT / p


SECRET_KEYS = ("GEMINI_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "COMFYUI_URL", "OLLAMA_URL",
               "GROQ_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")


def update_env_file(values: dict[str, str], env_file: Path = ROOT / ".env") -> None:
    """Set KEY=value lines in .env, keeping unrelated lines and comments; then drop cached settings.
    A value with a line break is refused: it would write extra KEY=value lines."""
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    pending = {k: v.strip() for k, v in values.items() if k in SECRET_KEYS}
    if any(ch in v for v in pending.values() for ch in "\r\n"):
        raise ValueError("a setting value cannot contain a line break")
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in pending:
            out.append(f"{key}={pending.pop(key)}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in pending.items()]
    env_file.write_text("\n".join(out) + "\n", encoding="utf-8")
    get_settings.cache_clear()


@lru_cache
def get_settings(config_file: Path = ROOT / "config.yaml") -> Settings:
    data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    return Settings(pipeline=PipelineConfig(**data), secrets=Secrets())
