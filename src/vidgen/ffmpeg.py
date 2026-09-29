"""Thin wrappers around ffmpeg/ffprobe subprocesses."""

import math
import shutil
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
        if shutil.which("ffmpeg") is None:
            raise FFmpegError("ffmpeg not found on PATH — install FFmpeg") from e
        # Windows reports an over-long command line (WinError 206) as "file not found"
        raise FFmpegError(f"could not start ffmpeg ({e}); command line length {len(' '.join(cmd))}") from e
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(f"ffmpeg timed out after {timeout:.0f}s: {' '.join(cmd)}") from e
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr[-2000:]}")


# what every phone and platform plays: 4:2:0, limited ("tv") range, BT.709, tagged as such (x264 writes the
# range flag only with the whole colour description)
DELIVERY_ARGS = ("-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709",
                 "-color_trc", "bt709")


@lru_cache
def video_encoder() -> tuple[str, ...]:
    """NVENC if it actually encodes on this machine (listing alone is not proof), else libx264."""
    try:
        run(["-f", "lavfi", "-i", "color=s=256x256:d=0.1", "-c:v", "h264_nvenc", "-f", "null", "-"])
        return ("-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0", *DELIVERY_ARGS)
    except FFmpegError:
        return ("-c:v", "libx264", "-preset", "veryfast", "-crf", "20", *DELIVERY_ARGS)


def volume_stats(path: Path) -> tuple[float | None, float | None]:
    """(mean, max) level in dBFS from ffmpeg volumedetect; None for what can't be measured or is silence."""
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "volumedetect",
                           "-vn", "-f", "null", "-"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    found: dict[str, float | None] = {"mean_volume": None, "max_volume": None}
    for line in proc.stderr.splitlines():
        for key in found:
            if f"{key}:" in line:
                try:
                    value = float(line.split(f"{key}:")[1].split("dB")[0])
                except ValueError:
                    continue
                found[key] = value if math.isfinite(value) else None
    return found["mean_volume"], found["max_volume"]


def mean_volume(path: Path) -> float | None:
    return volume_stats(path)[0]


def duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    try:
        return float(proc.stdout.strip())
    except ValueError as e:
        raise FFmpegError(f"ffprobe could not read duration of {path}: {proc.stderr}") from e
