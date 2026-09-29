"""Scene-change transitions that match the sound at the cut (kinetic motion, phase 1).

A whoosh heard over a soft cross-fade feels wrong, and the same fade at every scene reads as a template.
So the xfade used at each scene change follows the sound effect planned there (sfx.detect_cues):
- whoosh → a directional move (smoothleft / smoothright / wipetl), never the same one twice in a row
- impact → zoomin into a still, a dip through black on video
- an evidence card (visuals/document.py) always slides up, like a page brought into view
- anything else → the soft cross-fade ("fade"; xfade's "dissolve" is a grainy pixel dither)

Choices are deterministic (from the cue's seeded variant), so a re-render gives the same video.
Straight cuts are decided earlier, in clips.plan_shots, because the sound plan needs the cut times.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vidgen.assemble.sfx import Cue

if TYPE_CHECKING:
    from vidgen.assemble.clips import Shot

DEFAULT = "fade"
CARD = "slideup"
WHOOSH = ("smoothleft", "smoothright", "wipetl")
IMPACT = ("zoomin", "fadeblack")   # still / video
CUE_WINDOW = 0.35                  # a cue this close to a scene change belongs to it (seconds)


def pick_transition(prev: Shot, cur: Shot, cue: Cue | None, last: str) -> str:
    """The xfade into `cur`; `last` is the most recent move (not counting soft fades): never repeated."""
    if cur.card:
        return CARD
    if cue is not None and cue.kind == "whoosh":
        k = cue.variant % len(WHOOSH)
        return WHOOSH[k] if WHOOSH[k] != last else WHOOSH[(k + 1) % len(WHOOSH)]
    if cue is not None and cue.kind == "impact":
        choice, other = IMPACT if cur.kind == "image" else IMPACT[::-1]
        return choice if choice != last else other
    return DEFAULT


def assign_transitions(shots: list[Shot], cues: list[Cue], fps: int) -> list[str]:
    """Set `transition` on every shot that a scene change cross-fades into; returns them in order."""
    chosen: list[str] = []
    last, frames = DEFAULT, 0
    for prev, cur in zip(shots, shots[1:]):
        frames += prev.frames
        if not cur.transition_in:
            continue
        cut = frames / fps
        near = [c for c in cues if abs(c.time - cut) <= CUE_WINDOW]
        cue = min(near, key=lambda c: abs(c.time - cut), default=None)
        cur.transition = pick_transition(prev, cur, cue, last)
        if cur.transition != DEFAULT:   # fades between two whooshes must not let the same move repeat
            last = cur.transition
        chosen.append(cur.transition)
    return chosen
