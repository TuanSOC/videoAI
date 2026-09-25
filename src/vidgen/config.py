"""Settings: secrets from .env, pipeline options from config.yaml."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]

Format = Literal["short", "long"]
Lang = Literal["vi", "en"]


class FormatPreset(BaseModel):
    width: int
    height: int
    fps: int
    target_seconds: tuple[int, int]
    max_ai_video: int

    @property
    def orientation(self) -> str:
        return "portrait" if self.height > self.width else "landscape"


class LLMConfig(BaseModel):
    gemini_model: str = "gemini-flash-latest"
    ollama_model: str = "qwen2.5:7b"


class WhisperConfig(BaseModel):
    model: str = "small"
    device: str = "cuda"
    compute_type: str = "float16"


class AIConfig(BaseModel):
    image_size: dict[str, tuple[int, int]] = {"short": (768, 1344), "long": (1344, 768)}
    video_size: dict[str, tuple[int, int]] = {"short": (544, 960), "long": (960, 544)}
    video_frames: int = 81
    timeout_seconds: int = 1800


class PipelineConfig(BaseModel):
    output_dir: Path = Path("output")
    cache_dir: Path = Path("cache")
    formats: dict[str, FormatPreset]
    voices: dict[str, str]
    llm: LLMConfig = LLMConfig()
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
    """Set KEY=value lines in .env, keeping unrelated lines and comments; then drop cached settings."""
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    pending = {k: v.strip() for k, v in values.items() if k in SECRET_KEYS}
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
