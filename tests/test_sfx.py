import shutil
from pathlib import Path

import pytest

from vidgen.assemble import sfx
from vidgen.assemble.render import audio_filter, final_args
from vidgen.config import get_settings
from vidgen.models import Scene, SceneAudio, Script, Timeline, WordTiming

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def make(sentences: list[str], lang="vi", gap=0.0):
    """One scene per sentence; words 0.3 s apart; `gap` extra seconds between scenes."""
    scenes, audio, t = [], [], 0.0
    for i, text in enumerate(sentences, 1):
        start = t
        words = []
        for tok in text.split():
            words.append(WordTiming(word=tok.strip(".,!?"), start=t, end=t + 0.25))
            t += 0.3
        t += gap
        scenes.append(Scene(id=i, narration=text, visual_query="q"))
        audio.append(SceneAudio(scene_id=i, path="", start=start, duration=t - start, words=words))
    return Script(title="t", hook="h", lang=lang, format="short", scenes=scenes), Timeline(scenes=audio)


def kinds(cues, kind):
    return [round(c.time, 2) for c in cues if c.kind == kind]


# --- detection -------------------------------------------------------------------------------------
def test_whoosh_just_before_each_cut_with_spacing():
    script, tl = make(["Một hai ba bốn năm sáu bảy tám chín mười."] * 6)
    cues = sfx.detect_cues(script, tl, cut_times=[6.0, 8.0, 17.0])   # 8.0 is too close to 6.0
    half = sfx.TRANSITION / 2                                        # peak mid-dissolve
    assert kinds(cues, "whoosh") == [round(6.0 - half, 2), round(17.0 - half, 2)]


def test_hook_gets_an_impact_and_shock_words_are_capped():
    script, tl = make(["Con tàu biến mất không dấu vết.", "Bí mật nằm dưới đáy biển sâu thẳm.",
                       "Đây là vùng biển nguy hiểm bậc nhất.", "Một bí mật khác đang chờ.",
                       "Sự thật gây chấn động cả thế giới."], gap=2.0)
    cues = sfx.detect_cues(script, tl, cut_times=[])
    impacts = kinds(cues, "impact")
    assert impacts[0] == 0.0                                      # the hook opens with an impact
    assert len(impacts) == 2                                      # cap
    assert impacts[1] - impacts[0] >= sfx.DENSITY["subtle"].impact_spacing


def test_english_shock_words():
    script, tl = make(["Everyone uses this app daily.", "Nobody knew the secret was deadly."], lang="en", gap=8.0)
    cues = sfx.detect_cues(script, tl, cut_times=[])
    shock = [c for c in cues if c.kind == "impact" and c.time > 0]
    assert shock and abs(shock[0].time - tl.scenes[1].words[0].start) < 0.01  # "Nobody"


def test_pops_on_numbers_years_and_percent_with_cap_and_spacing():
    script, tl = make(["Mạng botnet 1.5 triệu máy từ năm 2016 chiếm 30% lưu lượng.",
                       "Có 12 vụ trong 3 tháng và 45 nạn nhân.", "Thêm 7 vụ nữa, 8 vụ nữa, 9 vụ nữa."], gap=4.0)
    pops = kinds(sfx.detect_cues(script, tl, cut_times=[]), "pop")
    assert 0 < len(pops) <= sfx.DENSITY["subtle"].pop_cap
    assert all(b - a >= sfx.DENSITY["subtle"].pop_spacing for a, b in zip(pops, pops[1:]))


def test_collisions_keep_the_stronger_cue():
    script, tl = make(["Năm 2016 là bí mật."])
    # a cut right on the first number: impact (hook at 0.0) and pop (2016 at 0.3) and whoosh collide
    cues = sfx.detect_cues(script, tl, cut_times=[0.5])
    times = sorted(c.time for c in cues)
    assert all(b - a >= sfx.MIN_GAP for a, b in zip(times, times[1:]))
    assert cues[0].kind == "impact" and cues[0].time == 0.0


def test_density_presets():
    script, tl = make(["Bí mật 2016 biến mất."] * 6, gap=3.0)
    cuts = [tl.scenes[i].start for i in range(1, 6)]
    minimal = sfx.detect_cues(script, tl, cuts, density="minimal")
    dense = sfx.detect_cues(script, tl, cuts, density="dense")
    assert [c.kind for c in minimal].count("impact") == 1 and not kinds(minimal, "pop")
    assert len(kinds(minimal, "whoosh")) <= 2
    subtle = sfx.detect_cues(script, tl, cuts, density="subtle")
    assert [c.kind for c in subtle].count("impact") == 2                     # capped
    assert [c.kind for c in dense].count("impact") > 2                       # not capped


def test_detection_is_deterministic_and_sorted():
    script, tl = make(["Bí mật 2016.", "Nguy hiểm 30%."], gap=5.0)
    a = sfx.detect_cues(script, tl, [2.0], seed="x")
    assert a == sfx.detect_cues(script, tl, [2.0], seed="x")
    assert [c.time for c in a] == sorted(c.time for c in a)


def test_cut_times_from_shots():
    from vidgen.assemble.clips import Shot, cut_times

    shots = [Shot(1, 0, "video", None, 0, 60, "none"), Shot(1, 1, "video", None, 0, 30, "none"),
             Shot(2, 0, "video", None, 0, 90, "none", transition_in=True), Shot(3, 0, "color", None, 0, 30, "none")]
    assert cut_times(shots, 30) == [3.0]          # only real dissolves between scenes


# --- library & track ---------------------------------------------------------------------------------
@needs_ffmpeg
def test_library_synthesised_once_and_user_files_win(tmp_path):
    lib = sfx.ensure_library(tmp_path)
    assert all(len(lib[k]) == sfx.VARIANTS for k in sfx.FOLDERS)
    assert all(p.suffix == ".wav" and p.stat().st_size > 1000 for paths in lib.values() for p in paths)
    mtime = lib["pop"][0].stat().st_mtime
    assert sfx.ensure_library(tmp_path)["pop"][0].stat().st_mtime == mtime   # not regenerated
    mine = tmp_path / sfx.FOLDERS["impact"] / "my_boom.mp3"
    shutil.copy(lib["impact"][0], mine)
    assert sfx.ensure_library(tmp_path)["impact"] == [mine]                  # user file replaces synth


@needs_ffmpeg
def test_sfx_track_has_the_video_length(tmp_path):
    from vidgen import ffmpeg

    lib = sfx.ensure_library(tmp_path / "lib")
    cues = [sfx.Cue("impact", 0.0), sfx.Cue("whoosh", 2.0, 5), sfx.Cue("pop", 4.5, 1)]
    out = sfx.build_sfx_track(cues, lib, 6.0, tmp_path / "sfx.wav")
    assert out is not None and ffmpeg.duration(out) == pytest.approx(6.0, abs=0.05)
    assert sfx.build_sfx_track([], lib, 6.0, tmp_path / "none.wav") is None


# --- mix ---------------------------------------------------------------------------------------------
def test_final_args_mixes_sfx_under_a_limiter():
    args = final_args(30.0, Path("m.mp3"), music_start=0.0, music_gain_db=-21.5, sfx=Path("sfx.wav"))
    graphs = [args[k + 1] for k, a in enumerate(args) if a == "-filter_complex"]
    assert len(graphs) == 2 and "[0:v]" in graphs[0]                      # NaN fix: separate graphs
    audio = graphs[1]
    assert "[3:a]" in audio and "sfx.wav" in args and "alimiter" in audio
    assert "volume=-21.5dB" in audio and "sidechaincompress" in audio
    no_music = final_args(30.0, None, sfx=Path("sfx.wav"))
    audio2 = [no_music[k + 1] for k, a in enumerate(no_music) if a == "-filter_complex"][1]
    assert "[2:a]" in audio2 and "sidechaincompress" not in audio2 and "alimiter" in audio2
    plain = final_args(30.0, None)
    assert "sfx.wav" not in plain and audio_filter(False).count("[a]") == 1


def test_music_gain_puts_music_18db_under_voice():
    from vidgen.assemble.render import FALLBACK_MUSIC_DB, music_gain_db

    assert music_gain_db(voice_mean=-20.0, music_mean=-14.0) == pytest.approx(-24.0)
    assert music_gain_db(None, -14.0) == pytest.approx(FALLBACK_MUSIC_DB)


def test_sfx_config_defaults():
    cfg = get_settings().pipeline.sfx
    assert cfg.enabled is True and cfg.density in sfx.DENSITY
