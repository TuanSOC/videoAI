"""Look at a few frames of a shot: where is the subject (for cropping) and how bright is it (for
matching exposure across clips from different stock photographers).

Subject position = the horizontal window with the most motion (frame differences) plus detail
(edges). Cheap, no model download, and good enough to stop cropping an octopus off the edge when a
landscape clip is cut to 9:16."""

import logging
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

log = logging.getLogger(__name__)
SAMPLES = 5
ANALYSIS_WIDTH = 320
DETECTED_WEIGHT = 0.75   # blend with the centre: a wrong guess should not swing to a far edge
TARGET_LUMA = 0.46       # 0..1 mean luma everything is nudged toward
MAX_BRIGHTNESS_SHIFT = 0.10


@dataclass
class Focus:
    x: float = 0.5          # 0 = crop from the left edge, 1 = from the right edge
    brightness: float = 0.0  # ffmpeg eq brightness offset (-1..1)


def _frames(path: Path, kind: str, start: float, duration: float) -> list[np.ndarray]:
    if kind == "image":
        img = cv2.imread(str(path))  # imread handles most paths; fall back for unicode paths
        if img is None:
            img = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        return [img] if img is not None else []
    cap = cv2.VideoCapture(str(path))
    frames = []
    try:
        for k in range(SAMPLES):
            cap.set(cv2.CAP_PROP_POS_MSEC, 1000 * (start + duration * k / max(SAMPLES - 1, 1)))
            ok, frame = cap.read()
            if ok:
                frames.append(frame)
    finally:
        cap.release()
    return frames


def _small_gray(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (ANALYSIS_WIDTH, max(1, round(h * ANALYSIS_WIDTH / w))))
    return cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0


def best_window(energy: np.ndarray, frac: float) -> float:
    """Left position (0..1 of the free space) of the window of width `frac` holding the most energy."""
    n = len(energy)
    win = max(1, round(n * frac))
    if win >= n or energy.sum() <= 0:
        return 0.5
    sums = np.convolve(energy, np.ones(win), mode="valid")
    return float(np.argmax(sums)) / (n - win)


def analyse(path: Path, kind: str, start: float, duration: float, out_aspect: float) -> Focus:
    """out_aspect = width/height of the output (0.5625 for 9:16)."""
    try:
        frames = _frames(path, kind, start, duration)
    except Exception as e:  # unreadable file: neutral framing, no correction
        log.warning("focus analysis failed for %s: %s", path, e)
        return Focus()
    if not frames:
        return Focus()
    grays = [_small_gray(f) for f in frames]
    luma = float(np.mean([g.mean() for g in grays]))
    brightness = float(np.clip((TARGET_LUMA - luma) * 0.5, -MAX_BRIGHTNESS_SHIFT, MAX_BRIGHTNESS_SHIFT))

    h, w = grays[0].shape
    frac = (out_aspect * h) / w  # share of the source width that survives the crop
    if frac >= 0.98:
        return Focus(0.5, brightness)  # same shape as the output: nothing to choose
    edges = sum(np.abs(cv2.Laplacian(g, cv2.CV_32F)) for g in grays) / len(grays)
    motion = (sum(np.abs(b - a) for a, b in zip(grays, grays[1:])) / max(len(grays) - 1, 1)
              if len(grays) > 1 else np.zeros_like(grays[0]))
    energy = (edges + 2.0 * motion).sum(axis=0)  # moving things are the subject more often than texture
    x = best_window(energy, frac)
    return Focus(0.5 + DETECTED_WEIGHT * (x - 0.5), brightness)
