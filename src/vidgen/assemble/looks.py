"""One colour look per video, so clips from different photographers read as one film.

The writer picks a look for the topic (like the music mood); a series can pin one for the whole channel
(series/*.yaml `look`, stored in state.json). Evidence cards keep the neutral grade: tinting the paper would
make the quoted source look doctored. All looks end in the same fine grain.
"""

# per look: extra eq settings (brightness offset, contrast, saturation, gamma), a colorbalance / curves step,
# and the vignette angle (larger = darker corners)
LOOKS: dict[str, dict] = {
    # the grade every video had before looks: mild contrast/saturation lift
    "neutral": {"eq": (0.0, 1.04, 1.08, 1.0), "tone": "", "vignette": 0.45, "grain": 3},
    # mystery, crime, the unexplained: cool shadows, deeper blacks, less colour
    "dark_mystery": {"eq": (-0.01, 1.1, 0.82, 0.97), "tone": "colorbalance=rs=-0.04:bs=0.09:bm=0.04:rh=-0.02",
                     "vignette": 0.62, "grain": 4},
    # cyber security, AI, tech: teal shadows, warm highlights (teal & orange)
    "cyber_tech": {"eq": (0.0, 1.08, 1.12, 1.0),
                   "tone": "colorbalance=rs=-0.13:gs=0.03:bs=0.16:rm=-0.03:bm=0.04:rh=0.11:gh=0.02:bh=-0.1",
                   "vignette": 0.5,
                   "grain": 3},
    # history, war, archives: half-way to sepia, faded blacks, visible grain (curves' "vintage" preset turned
    # shadows magenta)
    "vintage_archive": {"eq": (0.0, 1.0, 0.85, 1.0),
                        "tone": "colorchannelmixer=.697:.385:.095:0:.175:.843:.084:0:.136:.267:.566,"
                                "curves=all='0/0.06 1/0.96'", "vignette": 0.55, "grain": 7},
}
DEFAULT = "neutral"


def normalize_look(look: str | None) -> str:
    look = (look or "").strip().casefold()
    return look if look in LOOKS else DEFAULT


def grade(look: str, brightness: float) -> str:
    """The FFmpeg filter chain for `look`, with the shot's exposure correction (focus.py) folded in."""
    spec = LOOKS[normalize_look(look)]
    offset, contrast, saturation, gamma = spec["eq"]
    eq = f"eq=brightness={brightness + offset:.3f}:contrast={contrast}:saturation={saturation}"
    if gamma != 1.0:
        eq += f":gamma={gamma}"
    parts = [eq, spec["tone"], f"vignette=angle={spec['vignette']}", f"noise=alls={spec['grain']}:allf=t"]
    return ",".join(p for p in parts if p)
