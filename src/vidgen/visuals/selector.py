"""Per-scene visual sourcing. Placeholder until phase 4 adds stock → Flux → Wan fallback chain."""

from pathlib import Path

from vidgen.config import FormatPreset, Settings
from vidgen.models import Asset, Script


def source_visuals(script: Script, preset: FormatPreset, out_dir: Path, s: Settings) -> list[Asset]:
    return [Asset(scene_id=sc.id, path="", kind="color", source="placeholder") for sc in script.scenes]
