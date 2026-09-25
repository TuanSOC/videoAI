"""Background music: user-supplied royalty-free tracks in assets/music/ (Pixabay Music, YouTube Audio Library)."""

import random
from pathlib import Path

AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".ogg"}


def pick_music(music_dir: Path, seed: str) -> Path | None:
    """Deterministic per video (seeded by slug) so re-renders keep the same track."""
    tracks = sorted(p for p in music_dir.rglob("*") if p.suffix.lower() in AUDIO_EXTS)
    return random.Random(seed).choice(tracks) if tracks else None
