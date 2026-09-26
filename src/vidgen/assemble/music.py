"""Background music: user-supplied royalty-free tracks in assets/music/ (Pixabay Music, YouTube Audio Library),
sorted into mood folders (assets/music/tense/, calm/, ...). The script model tags each video with a mood."""

import random
from pathlib import Path

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".ogg"}
MOODS = ("tense", "calm", "upbeat", "mystery", "inspiring")


def _tracks(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in AUDIO_EXTS) if folder.is_dir() else []


def pick_music(music_dir: Path, seed: str, mood: str = "") -> Path | None:
    """A track from the mood's folder, else from anywhere; deterministic per video (seeded by slug) so
    re-renders keep the same track."""
    tracks = (_tracks(music_dir / mood) if mood else []) or _tracks(music_dir)
    return random.Random(seed).choice(tracks) if tracks else None


def music_start(track_seconds: float, video_seconds: float, seed: str) -> float:
    """Where to start in the track: tracks often open with a long intro; any point that leaves the whole
    video (plus a second) inside the track. Shorter tracks start at 0 and loop."""
    room = track_seconds - video_seconds - 1
    return round(random.Random(seed + "/start").uniform(0, room), 2) if room > 0 else 0.0
