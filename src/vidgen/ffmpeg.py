"""Thin wrappers around ffmpeg/ffprobe subprocesses."""

import subprocess
from functools import lru_cache
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def run(args: list[str], cwd: Path | None = None, timeout: float | None = None) -> None:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args]
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except FileNotFoundError as e:
        raise FFmpegError("ffmpeg not found on PATH — install FFmpeg") from e
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(f"ffmpeg timed out after {timeout:.0f}s: {' '.join(cmd)}") from e
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-2000:]}")


@lru_cache
def video_encoder() -> tuple[str, ...]:
    """NVENC if it actually encodes on this machine (listing alone is not proof), else libx264."""
    try:
        run(["-f", "lavfi", "-i", "color=s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"])
        return ("-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0")
    except FFmpegError:
        return ("-c:v", "libx264", "-preset", "veryfast", "-crf", "20")


def mean_volume(path: Path) -> float | None:
    """Mean level in dBFS (ffmpeg volumedetect), or None if it can't be measured."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect",
                           "-vn", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in proc.stderr.splitlines():
        if "mean_volume:" in line:
            try:
                return float(line.split("mean_volume:")[1].split("dB")[0])
            except ValueError:
                return None
    return None


def duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return float(proc.stdout.strip())
    except ValueError as e:
        raise FFmpegError(f"ffprobe could not read duration of {path}: {proc.stderr}") from e
