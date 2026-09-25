"""Environment checks. Missing optional services are warnings, not errors."""

import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
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
        return httpx.get(url, timeout=1.5).status_code == 200
    except httpx.HTTPError:
        return False


def _ollama_models(url: str) -> list[str] | None:
    """Installed model names, or None when Ollama is unreachable."""
    try:
        resp = httpx.get(f"{url}/api/tags", timeout=1.5)
        return [m["name"] for m in resp.json().get("models", [])] if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


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

    sec, llm = s.secrets, s.pipeline.llm
    has_stock = bool(sec.pexels_api_key or sec.pixabay_api_key)
    checks.append(Check("PEXELS/PIXABAY key", has_stock, "set" if has_stock else "missing → no stock footage"))

    # probe both local services at once: on Windows a closed port takes ~2s to refuse
    with ThreadPoolExecutor(2) as pool:
        comfy_f = pool.submit(_http_ok, f"{sec.comfyui_url}/system_stats")
        ollama_f = pool.submit(_ollama_models, sec.ollama_url)
        comfy, models = comfy_f.result(), ollama_f.result()
    checks.append(Check("ComfyUI", comfy, sec.comfyui_url if comfy else "offline → no AI image/video", required=False))

    # per-provider rows are warnings; "LLM available" below is the one required gate
    usable: list[str] = []
    if "ollama" in llm.providers:
        if models is None:
            checks.append(Check("Ollama", False, "offline → open the Ollama app or run `ollama serve`",
                                required=False))
        elif llm.ollama_model not in models and f"{llm.ollama_model}:latest" not in models:
            checks.append(Check("Ollama", False, f"model missing → ollama pull {llm.ollama_model}",
                                required=False))
        else:
            checks.append(Check("Ollama", True, f"{llm.ollama_model} ready"))
            usable.append("ollama")
    if "gemini" in llm.providers:
        checks.append(Check("GEMINI_API_KEY", bool(sec.gemini_api_key),
                            "set (may bill past free quota)" if sec.gemini_api_key else "missing", required=False))
        if sec.gemini_api_key:
            usable.append("gemini")
    checks.append(Check("LLM available", bool(usable), ", ".join(usable) if usable
                        else "no usable provider in llm.providers"))

    fonts = list((ROOT / "assets" / "fonts").glob("*.ttf"))
    checks.append(Check("caption font", bool(fonts), fonts[0].name if fonts else "add a .ttf to assets/fonts/"))
    return checks
