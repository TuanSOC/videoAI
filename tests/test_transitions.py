"""Kinetic transitions, phase 1: the picture change matches the sound at the cut (a whoosh gets a whip/wipe,
an impact a zoom or a dip to black, an evidence card slides up); no cue keeps the soft cross-fade."""

import math
import re
from pathlib import Path

import pytest

from vidgen.assemble import transitions as tr
from vidgen.assemble.clips import MIN_SHOT, TRANSITION, Shot, _motion, cut_times, plan_shots, shot_args
from vidgen.assemble.sfx import Cue
from vidgen.config import get_settings
from vidgen.models import Asset, SceneAudio, Timeline

P = get_settings().preset("short")


def shot(kind="video", frames=90, card=False, scene=1):
    return Shot(scene, 0, kind, Path("x.mp4") if kind != "color" else None, 0.0, frames, "none",
                transition_in=True, card=card)


# --- choosing ------------------------------------------------------------------------------------------------
def test_no_cue_keeps_the_soft_cross_fade():
    assert tr.pick_transition(shot(), shot(), None, "fade") == "fade"


def test_an_evidence_card_slides_up():
    assert tr.pick_transition(shot(), shot(card=True), Cue("whoosh", 3.0, 1), "fade") == "slideup"


@pytest.mark.parametrize("variant", range(6))
def test_a_whoosh_gets_a_directional_move_and_never_the_same_twice(variant):
    cue = Cue("whoosh", 3.0, variant)
    first = tr.pick_transition(shot(), shot(), cue, "fade")
    assert first in tr.WHOOSH
    assert tr.pick_transition(shot(), shot(), cue, first) != first


def test_an_impact_zooms_into_a_still_and_dips_to_black_on_video():
    cue = Cue("impact", 3.0)
    assert tr.pick_transition(shot(), shot(kind="image"), cue, "fade") == "zoomin"
    assert tr.pick_transition(shot(), shot(kind="video"), cue, "fade") == "fadeblack"
    assert tr.pick_transition(shot(), shot(kind="image"), cue, "zoomin") == "fadeblack"   # no repeat


def test_a_pop_is_not_a_transition_sound():
    assert tr.pick_transition(shot(), shot(), Cue("pop", 3.0), "fade") == "fade"


def test_choice_is_repeatable():
    cue = Cue("whoosh", 3.0, 417)
    assert len({tr.pick_transition(shot(), shot(), cue, "fade") for _ in range(20)}) == 1


# --- wiring: cues planned from the shot plan, then transitions picked ---------------------------------------
def test_each_scene_change_takes_the_cue_that_lands_on_it():
    fps = P.fps
    shots = [shot(frames=90, scene=1), shot(frames=90, scene=2), shot(frames=90, scene=3, kind="image"),
             shot(frames=90, scene=4)]
    shots[0].transition_in = False
    cuts = cut_times(shots, fps)                                     # 3.0, 6.0, 9.0
    cues = [Cue("whoosh", round(cuts[0] - TRANSITION / 2, 3), 0),     # peaks mid-dissolve (sfx.detect_cues)
            Cue("impact", cuts[1] + 0.2),                             # a shock word right after the cut
            Cue("pop", cuts[2] + 1.5)]                                # nowhere near the third cut
    chosen = tr.assign_transitions(shots, cues, fps)
    assert chosen[0] in tr.WHOOSH and chosen[1] == "zoomin" and chosen[2] == "fade"
    assert [s.transition for s in shots[1:]] == chosen


def test_shot_args_uses_the_chosen_transition(tmp_path):
    a, b = shot(frames=90), shot(frames=90, scene=2)
    a.transition_in = False
    b.transition = "smoothleft"
    graph = " ".join(shot_args(a, b, P, tmp_path / "o.mp4"))
    assert "xfade=transition=smoothleft:" in graph


def test_a_very_short_scene_cuts_straight_out():
    fps = P.fps
    short = int(MIN_SHOT * fps) - 3
    tl = Timeline(scenes=[SceneAudio(scene_id=i, path="", start=s, duration=d, words=[])
                          for i, (s, d) in enumerate([(0.0, 3.0), (3.0, short / fps), (3.0 + short / fps, 3.0)], 1)])
    assets = [Asset(scene_id=i, path=f"visuals/s{i}.mp4", kind="video", source="pexels") for i in (1, 2, 3)]
    shots = plan_shots(tl, assets, P, "short", Path("o"), {})
    firsts = {s.scene_id: s for s in shots if s.index == 0}
    assert firsts[2].transition_in is True        # into the short scene: still a cross-fade
    assert firsts[3].transition_in is False       # out of it: a straight cut, it's too brief to dissolve out of


# --- camera: S-curve ease ------------------------------------------------------------------------------------
def test_ken_burns_eases_in_and_out():
    expr = _motion(Shot(1, 0, "image", Path("a.jpg"), 0.0, 90, "push"), P, 0, 90)
    assert "cos(PI*" in expr
    ease = lambda x: (1 - math.cos(math.pi * x)) / 2                  # the curve the expression encodes
    assert ease(0) == 0 and abs(ease(0.5) - 0.5) < 1e-9 and abs(ease(1) - 1) < 1e-9
    assert ease(0.05) < 0.05 and ease(0.95) > 0.95                    # slow start, slow stop


def test_eased_motion_still_continues_across_the_cross_fade():
    """The incoming shot starts at the progress already shown inside the previous segment (t0)."""
    expr = _motion(Shot(1, 0, "image", Path("a.jpg"), 0.0, 90, "pan_r", transition_in=True), P, 5, 95)
    assert re.search(r"on\+5\)/95", expr)


def test_moves_vary_even_with_soft_fades_between_them():
    """whoosh, (no cue), whoosh: the second whoosh must not repeat the first move (seen live: 3× smoothright)."""
    fps = P.fps
    shots = [shot(frames=90, scene=i) for i in range(1, 5)]
    shots[0].transition_in = False
    cuts = cut_times(shots, fps)
    cues = [Cue("whoosh", cuts[0], 1), Cue("whoosh", cuts[2], 1)]     # same variant: same first choice
    chosen = tr.assign_transitions(shots, cues, fps)
    assert chosen[1] == "fade" and chosen[0] in tr.WHOOSH and chosen[2] in tr.WHOOSH and chosen[0] != chosen[2]


# --- phase 2: a tripod clip drifts gently instead of sitting frozen -----------------------------------------
def test_a_tripod_clip_is_flagged_still_and_a_moving_one_is_not(monkeypatch):
    import numpy as np

    from vidgen.assemble import focus
    rng = np.random.default_rng(1)
    base = (rng.random((180, 320, 3)) * 255).astype(np.uint8)
    moving = [np.roll(base, 25 * k, axis=1) for k in range(5)]
    for frames, still in (([base] * 5, True), (moving, False)):
        monkeypatch.setattr(focus, "_frames", lambda *a, f=frames: f)
        assert focus.analyse(Path("c.mp4"), "video", 0, 3, 9 / 16).still is still
    monkeypatch.setattr(focus, "_frames", lambda *a: [base])
    assert focus.analyse(Path("i.jpg"), "image", 0, 3, 9 / 16).still is False     # stills have Ken Burns


def test_a_still_clip_drifts_on_the_same_eased_curve_and_a_moving_one_does_not():
    from vidgen.assemble.clips import _stream
    from vidgen.assemble.focus import Focus
    still = Shot(1, 0, "video", Path("a.mp4"), 0.0, 90, "none", focus=Focus(0.5, 0.0, still=True))
    moving = Shot(1, 0, "video", Path("a.mp4"), 0.0, 90, "none")
    drift = _stream(still, P, 5, 95, 90)
    assert "cos(PI*min(1,(n+5)/95))" in drift and "crop=" in drift
    assert "n+" not in _stream(moving, P, 5, 95, 90)


# --- phase 3: an impact mid-shot punches the frame in, then eases back ---------------------------------------
def test_impacts_on_a_scene_change_are_left_to_the_transition():
    from vidgen.assemble.render import punch_times
    cues = [Cue("impact", 0.0), Cue("impact", 6.1), Cue("impact", 12.4), Cue("whoosh", 20.0)]
    assert punch_times(cues, cuts=[6.0, 20.1]) == [0.0, 12.4]


def test_punch_zoom_rises_fast_and_eases_back():
    from vidgen.assemble.render import PUNCH, PUNCH_ATTACK, PUNCH_RELEASE, punch_filter
    f = punch_filter([2.0], P)
    assert f.startswith("zoompan=") and f"s={P.width}x{P.height}" in f and f"fps={P.fps}" in f
    assert "2.000" in f and f"{PUNCH}" in f
    assert punch_filter([], P) == ""
    # the envelope the expression encodes: 0 before, 1 at the top, back to 0 after the release
    def env(t, t0=2.0):
        if t0 <= t < t0 + PUNCH_ATTACK:
            return math.sin(math.pi / 2 * (t - t0) / PUNCH_ATTACK)
        if t0 + PUNCH_ATTACK <= t <= t0 + PUNCH_ATTACK + PUNCH_RELEASE:
            return (1 + math.cos(math.pi * (t - t0 - PUNCH_ATTACK) / PUNCH_RELEASE)) / 2
        return 0.0
    assert env(1.9) == 0 and abs(env(2.1) - 1) < 1e-9 and env(2.6) < 1e-9 and env(3.0) == 0


def test_final_pass_punches_before_the_captions_are_burned():
    from vidgen.assemble.render import final_args
    args = final_args(30.0, None, video_filter="zoompan=z='1'")
    graph = args[args.index("-filter_complex") + 1]
    assert graph.index("zoompan") < graph.index("ass=subs.ass")
    plain = final_args(30.0, None)
    assert plain[plain.index("-filter_complex") + 1] == "[0:v]ass=subs.ass:fontsdir=fonts[v]"
