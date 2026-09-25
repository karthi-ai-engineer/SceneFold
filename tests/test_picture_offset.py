"""Matching clips by their pictures, and turning the differences into one delay per clip."""

import numpy as np
import pytest
import synth

from scenefold import picture_offset
from scenefold.picture_offset import FPS, PictureMatch, match_pictures, metres, solve_delays

FRAME_S = 1 / 30


def filmed(curve, start_s, seconds, *, drift_ppm=0.0, seed=1, noise=0.5):
    phone = synth.Phone(start_s=start_s, seconds=seconds, drift_ppm=drift_ppm, seed=seed)
    return synth.filmed(curve, phone, noise=noise)


def test_finds_the_lag_between_two_clips_of_one_show():
    show = synth.lighting(180, seed=3)
    a, b = filmed(show, 0, 150, seed=1), filmed(show, 22.5, 120, seed=2)

    found = match_pictures(a, b, around_s=22.5 + 0.18)  # the sound put it a little off

    assert found is not None
    assert found.lag_s == pytest.approx(22.5, abs=FRAME_S)
    assert found.clearness > 4
    assert found.overlap_s == pytest.approx(120, abs=1)


def test_searches_only_around_where_the_sound_put_it():
    show = synth.lighting(180, seed=3)
    a, b = filmed(show, 0, 150, seed=1), filmed(show, 22.5, 120, seed=2)

    found = match_pictures(a, b, around_s=60.0, search_s=2.0)  # nowhere near the true 22.5 s

    # the best lag inside that window leads nothing, so there is no answer rather than a wrong one
    assert found is None


def test_a_steady_room_gives_no_clear_answer():
    """Jiku's venue is lit steadily: no pair reached even 3 standard deviations."""
    steady = np.full(int(180 * 30), 120.0)
    a, b = filmed(steady, 0, 150, seed=1), filmed(steady, 22.5, 120, seed=2)

    found = match_pictures(a, b, around_s=22.5)

    assert found is None or found.clearness < 4


def test_two_different_nights_do_not_match():
    a = filmed(synth.lighting(180, seed=3), 0, 150, seed=1)
    b = filmed(synth.lighting(180, seed=9), 22.5, 120, seed=2)

    found = match_pictures(a, b, around_s=22.5)

    assert found is None or found.clearness < 4


def test_too_little_overlap_is_refused():
    show = synth.lighting(180, seed=3)
    a, b = filmed(show, 0, 30, seed=1), filmed(show, 18, 30, seed=2)  # they share 12 seconds

    assert match_pictures(a, b, around_s=18, min_overlap_s=5) is not None
    assert match_pictures(a, b, around_s=18, min_overlap_s=30) is None


def test_a_clip_that_never_overlaps_gives_nothing():
    show = synth.lighting(300, seed=3)
    a, b = filmed(show, 0, 60, seed=1), filmed(show, 200, 60, seed=2)

    assert match_pictures(a, b, around_s=200) is None


def test_clock_drift_is_cancelled_like_it_is_for_sound():
    show = synth.lighting(400, seed=4)
    a = filmed(show, 0, 300, seed=1)
    b = filmed(show, 20, 260, drift_ppm=600, seed=2)

    corrected = match_pictures(a, b, around_s=20, drift_ppm=600)
    as_is = match_pictures(a, b, around_s=20)

    assert corrected is not None and as_is is not None
    assert corrected.lag_s == pytest.approx(20, abs=FRAME_S)
    assert corrected.clearness > as_is.clearness


def test_one_delay_per_clip_comes_out_of_the_pairs():
    late = {"a": 0.0, "b": 0.204, "c": 0.264, "d": 0.038}
    rng = np.random.default_rng(0)
    differences = [
        (x, y, late[y] - late[x] + rng.normal(0, 0.004), 5.0)
        for i, x in enumerate("abcd")
        for y in "abcd"[i + 1 :]
    ]

    found = solve_delays(list("abcd"), differences)

    assert all(found.used)
    assert min(found.heard_late_s.values()) == 0  # counted from the clip nearest the sound
    for clip, delay in late.items():
        assert found.heard_late_s[clip] == pytest.approx(delay, abs=0.01)
    assert found.worst_residual_s < 0.01


def test_a_pair_that_disagrees_with_the_rest_is_dropped():
    late = {"a": 0.0, "b": 0.2, "c": 0.3, "d": 0.05}
    differences = [
        (x, y, late[y] - late[x], 5.0) for i, x in enumerate("abcd") for y in "abcd"[i + 1 :]
    ]
    differences[2] = ("a", "d", 0.9, 5.0)  # a wrong match, 300 metres out

    found = solve_delays(list("abcd"), differences)

    assert not found.used[2]
    for clip, delay in late.items():
        assert found.heard_late_s[clip] == pytest.approx(delay, abs=0.005)


def test_clips_the_pictures_never_join_get_no_answer():
    differences = [("a", "b", 0.2, 5.0), ("b", "c", 0.1, 5.0)]

    found = solve_delays(list("abcde"), differences)

    assert sorted(found.heard_late_s) == ["a", "b", "c"]
    assert found.heard_late_s["a"] == pytest.approx(0.0, abs=1e-9)
    assert found.heard_late_s["c"] == pytest.approx(0.3, abs=1e-9)


def test_nothing_measured_means_nothing_claimed():
    found = solve_delays(list("abc"), [])

    assert found.heard_late_s == {}
    assert found.worst_residual_s is None


def test_delays_read_as_distance():
    assert metres(0.204) == pytest.approx(70, abs=1)
    assert metres(0.0) == 0


def test_brightness_is_read_frame_by_frame(tmp_path, monkeypatch):
    width, height = picture_offset.GRID
    frames = [bytes([10]) * (width * height), bytes([200]) * (width * height)]
    monkeypatch.setattr(picture_offset.media, "grey_frames", lambda *_: iter(frames))

    curve = picture_offset.load_brightness(tmp_path / "clip.mp4")

    assert curve.tolist() == [10, 200]


def test_a_match_knows_what_it_is():
    match = PictureMatch(lag_s=1.0, clearness=5.0, overlap_s=30.0)

    assert (match.lag_s, match.clearness, match.overlap_s) == (1.0, 5.0, 30.0)


def test_a_pattern_that_repeats_is_not_a_match():
    """Video encoding marks every keyframe, a second apart. That matches at every second, so it
    must not be read as the clips lining up: this is what failed on a steadily lit room."""
    rng = np.random.default_rng(4)
    seconds, fps = 120, 30
    beat = np.tile(np.concatenate([[14.0], np.zeros(fps - 1)]), seconds)  # a mark every second
    a = beat + rng.standard_normal(len(beat)) * 0.4
    b = beat + rng.standard_normal(len(beat)) * 0.4

    found = match_pictures(a[: 90 * fps], b[18 * fps : 108 * fps], around_s=0.0)

    assert found is None


def test_a_real_match_still_wins_through_a_repeating_pattern():
    """The same keyframe marks, but the clips really do share a show: the match must survive."""
    show = synth.lighting(200, seed=11)
    beat = np.tile(np.concatenate([[6.0], np.zeros(29)]), len(show) // 30 + 1)[: len(show)]
    lit = show + beat
    a = filmed(lit, 0, 150, seed=1)
    b = filmed(lit, 22.5, 120, seed=2)

    found = match_pictures(a, b, around_s=22.5)

    assert found is not None
    assert found.lag_s == pytest.approx(22.5, abs=FRAME_S)
    assert found.clearness > 4


def test_a_picture_that_never_changes_is_refused_before_it_is_matched():
    """A flat curve carries nothing to match, and encoding noise on two of them can line up by luck.

    This is not hypothetical: the same commit passed on one machine and failed on another, because
    the encoders left different traces of noise on a steadily lit room.
    """
    rng = np.random.default_rng(4)
    flat = np.full(int(40 * FPS), 120.0)
    noise = flat + rng.standard_normal(len(flat)) * 0.004  # what an encoder leaves behind

    assert match_pictures(flat, flat.copy(), 0.0) is None
    assert match_pictures(noise, noise + rng.standard_normal(len(flat)) * 0.004, 0.0) is None
    # a real lighting curve, the same length, still matches
    lit = synth.lighting(40, seed=3, fps=FPS)
    assert match_pictures(lit, lit.copy(), 0.0) is not None
