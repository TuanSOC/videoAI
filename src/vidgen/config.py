"""Settings: secrets from .env, pipeline options from config.yaml."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field
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


class LLMConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in config.yaml is an error, not ignored
    providers: list[Literal["ollama", "gemini"]] = ["ollama"]
    ollama_model: str = "qwen3:8b"
    ollama_think: bool = False
    gemini_model: str = "gemini-2.5-flash"


class VisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in config.yaml is an error, not ignored
    enabled: bool = True           # score stock thumbnails with a local vision model before picking
    model: str = "qwen2.5vl:7b"    # "qwen2.5vl:3b" is faster and lighter, less accurate


class SfxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo in config.yaml is an error, not ignored
    enabled: bool = True
    density: Literal["minimal", "subtle", "dense"] = "subtle"


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
    research: bool = True  # ground scripts in Wikipedia passages (script/research.py)
    ai: AIConfig = AIConfig()
    whisper: WhisperConfig = WhisperConfig()


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    gemini_api_key: str = ""
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


SECRET_KEYS = ("GEMINI_API_KEY", "PEXELS_API_KEY", "PIXABAY_API_KEY", "COMFYUI_URL", "OLLAMA_URL")


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
