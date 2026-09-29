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
