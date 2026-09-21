"""Finding the exact moments in a clip: does it catch what happened, and when?"""

import numpy as np
import pytest
import synth
from conftest import needs_ffmpeg, run_ffmpeg

from scenefold.moments import (
    PICTURE,
    SOUND,
    Moment,
    picture_moments,
    snap,
    sound_moments,
    strongest,
)

CLAPS = [1.5, 4.25, 7.0]
FLASHES = [2.0, 5.5]


@pytest.fixture(scope="module")
def clapping(tmp_path_factory):
    """Ten seconds of crowd noise with claps at times we chose."""
    rate = synth.RATE
    rng = np.random.default_rng(1)
    sound = rng.standard_normal(10 * rate).astype(np.float32) * 0.05  # a steady room
    for at in CLAPS:
        start = int(at * rate)
        clap = rng.standard_normal(int(0.02 * rate)) * 0.9
        sound[start : start + len(clap)] += clap.astype(np.float32)
    return synth.write_wav(tmp_path_factory.mktemp("claps") / "clapping.wav", sound)


@pytest.fixture(scope="module")
def flashing(tmp_path_factory):
    """Ten seconds of a dim picture with two flashes, drawn by FFmpeg."""
    path = tmp_path_factory.mktemp("flashes") / "flashing.mp4"
    lights = "+".join(f"0.7*lt(abs(t-{at})\\,0.06)" for at in FLASHES)
    run_ffmpeg("-v error -f lavfi -i color=c=0x202030:s=160x90:r=30:d=10",
               "-vf", f"eq=brightness='{lights}':eval=frame",
               "-c:v libx264 -preset ultrafast -pix_fmt yuv420p", path)  # fmt: skip
    return path


def test_claps_are_found_where_they_were_put(clapping):
    found = sound_moments(clapping)

    assert found, "three claps in a quiet room should be heard"
    for at in CLAPS:
        near = [m for m in found if abs(m.t_s - at) < 0.05]
        assert near, f"no moment found at {at} s"
    assert all(m.kind == SOUND for m in found)
    assert max(m.strength for m in found) == pytest.approx(1.0)


def test_steady_sound_holds_no_moments(tmp_path):
    """A room tone that never changes has nothing to time anything against."""
    rate = synth.RATE
    steady = (np.sin(2 * np.pi * 220 * np.arange(5 * rate) / rate) * 0.3).astype(np.float32)
    path = synth.write_wav(tmp_path / "steady.wav", steady)

    assert len(sound_moments(path)) <= 2  # the start of the tone, at most


@needs_ffmpeg
def test_flashes_are_found_where_they_were_drawn(flashing):
    found = picture_moments(flashing)

    assert found
    for at in FLASHES:
        near = [m for m in found if abs(m.t_s - at) < 0.15]
        assert near, f"no flash found at {at} s"
    assert all(m.kind == PICTURE for m in found)


def test_a_time_is_pulled_onto_the_nearest_strong_moment():
    moments = [
        Moment(t_s=4.0, kind=SOUND, strength=0.3),
        Moment(t_s=4.3, kind=SOUND, strength=1.0),
        Moment(t_s=9.0, kind=SOUND, strength=0.8),
    ]

    assert snap(4.2, moments).t_s == 4.3  # the strongest one close by, not the nearest
    assert snap(4.05, moments, within_s=0.1).t_s == 4.0  # ...within what was asked for
    assert snap(6.0, moments) is None  # nothing happened near here


def test_the_moment_that_stands_out_in_a_stretch():
    moments = [
        Moment(t_s=1.0, kind=SOUND, strength=0.4),
        Moment(t_s=5.0, kind=PICTURE, strength=0.9),
        Moment(t_s=12.0, kind=SOUND, strength=1.0),
    ]

    assert strongest(moments, 0, 10).t_s == 5.0
    assert strongest(moments, 0, 10).kind == PICTURE
    assert strongest(moments, 6, 11) is None
    assert strongest([], 0, 10) is None


def test_nothing_to_look_at_is_not_a_crash(tmp_path):
    silence = synth.write_wav(tmp_path / "silence.wav", np.zeros(synth.RATE, dtype=np.float32))

    assert sound_moments(silence) == []
