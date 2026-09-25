"""Environment checks. Missing optional services are warnings, not errors."""

import shutil
import subprocess
from dataclasses import dataclass

import httpx

from vidgen import ffmpeg
from vidgen.config import ROOT, Settings


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    required: bool = True


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _http_ok(url: str) -> bool:
    try:
        return httpx.get(url, timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


def run_checks(s: Settings) -> list[Check]:
    checks: list[Check] = []

    has_ffmpeg = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
    checks.append(Check("ffmpeg/ffprobe", has_ffmpeg, "on PATH" if has_ffmpeg else "install FFmpeg"))

    # a test encode, not the encoder list: an old NVIDIA driver lists NVENC but can't open it
    nvenc = has_ffmpeg and "h264_nvenc" in ffmpeg.video_encoder()
    checks.append(Check("h264_nvenc", nvenc, "GPU encode" if nvenc
                        else "fallback libx264 (update NVIDIA driver ≥570 for NVENC)", required=False))

    gpu = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"]).strip()
    checks.append(Check("NVIDIA GPU", bool(gpu), gpu or "not found (whisper/AI visuals on CPU or off)", required=False))

    sec = s.secrets
    checks.append(Check("GEMINI_API_KEY", bool(sec.gemini_api_key), "set" if sec.gemini_api_key else "missing → Ollama only", required=False))
    has_stock = bool(sec.pexels_api_key or sec.pixabay_api_key)
    checks.append(Check("PEXELS/PIXABAY key", has_stock, "set" if has_stock else "missing → no stock footage"))

    comfy = _http_ok(f"{sec.comfyui_url}/system_stats")
    checks.append(Check("ComfyUI", comfy, sec.comfyui_url if comfy else "offline → no AI image/video", required=False))
    ollama = _http_ok(f"{sec.ollama_url}/api/tags")
    checks.append(Check("Ollama", ollama, sec.ollama_url if ollama else "offline → no LLM fallback", required=False))
    has_llm = bool(sec.gemini_api_key) or ollama
    checks.append(Check("LLM available", has_llm, "ok" if has_llm else "set GEMINI_API_KEY or start Ollama"))

    fonts = list((ROOT / "assets" / "fonts").glob("*.ttf"))
    checks.append(Check("caption font", bool(fonts), fonts[0].name if fonts else "add a .ttf to assets/fonts/"))
    return checks
