"""Scoring a clip's picture: does it really prefer sharp, steady, well-exposed footage?"""

import numpy as np
import pytest
from conftest import needs_ffmpeg, run_ffmpeg

from scenefold import quality
from scenefold.quality import ClipQuality, combine, score_clip

SECONDS = 4
SOURCE = f"testsrc2=s=640x360:r=30:d={SECONDS}"  # a busy picture: plenty of detail to lose


@pytest.fixture(scope="module")
def clips(tmp_path_factory):
    """The same few seconds, filmed five ways."""
    folder = tmp_path_factory.mktemp("quality")
    ways = {
        "sharp": "null",
        "blurred": "gblur=sigma=6",
        # a phone being waved about: the whole picture jumps several times a second
        "shaky": "crop=560:300:x='280+120*sin(2*PI*t*6)':y='150+90*cos(2*PI*t*7)'",
        "blown": "eq=brightness=0.75",
        "dark": "eq=brightness=-0.75",
    }
    for name, filters in ways.items():
        run_ffmpeg("-v error -f lavfi -i", SOURCE, "-vf", filters,
                   "-c:v libx264 -preset ultrafast -pix_fmt yuv420p",
                   folder / f"{name}.mp4")  # fmt: skip
    return {name: score_clip(name, folder / f"{name}.mp4") for name in ways}


@needs_ffmpeg
def test_a_blurred_clip_is_less_sharp(clips):
    assert clips["sharp"].sharpness.mean() > clips["blurred"].sharpness.mean()


@needs_ffmpeg
def test_a_waved_about_clip_is_less_steady(clips):
    assert clips["sharp"].steadiness.mean() > clips["shaky"].steadiness.mean()


@needs_ffmpeg
def test_blown_out_and_crushed_clips_are_badly_exposed(clips):
    assert clips["sharp"].exposure.mean() > clips["blown"].exposure.mean()
    assert clips["sharp"].exposure.mean() > clips["dark"].exposure.mean()


@needs_ffmpeg
def test_the_good_clip_wins_overall(clips):
    scores = combine(list(clips.values()))

    best = max(scores, key=lambda clip_id: scores[clip_id].combined.mean())
    assert best == "sharp"
    for name in ("blurred", "shaky", "blown", "dark"):
        assert scores["sharp"].combined.mean() > scores[name].combined.mean(), name


@needs_ffmpeg
def test_one_score_per_second_of_footage(clips):
    for clip in clips.values():
        assert clip.seconds == SECONDS
        assert len(clip.steadiness) == SECONDS
        assert len(clip.exposure) == SECONDS


def test_scores_stay_between_zero_and_one():
    clips = [
        ClipQuality("a", np.array([1.0, 9.0]), np.array([0.2, 0.9]), np.array([0.1, 1.0])),
        ClipQuality("b", np.array([5.0, 5.0]), np.array([0.5, 0.5]), np.array([0.5, 0.5])),
    ]

    scores = combine(clips)

    assert set(scores) == {"a", "b"}
    for scored in scores.values():
        assert scored.combined.min() >= 0 and scored.combined.max() <= 1


def test_a_measure_that_never_varies_decides_nothing():
    """Every angle equally sharp must not hand the cut to whichever is listed first."""
    same = np.array([4.0, 4.0])
    clips = [
        ClipQuality("a", same, np.array([0.9, 0.9]), same / 8),
        ClipQuality("b", same, np.array([0.1, 0.1]), same / 8),
    ]

    scores = combine(clips)

    assert scores["a"].combined.mean() > scores["b"].combined.mean()  # steadiness alone
    steady = quality.WEIGHTS["steadiness"]
    assert scores["a"].combined.mean() == pytest.approx(0.5 * (1 - steady) + steady)


def test_nothing_to_score_is_not_a_crash():
    assert combine([]) == {}
    empty = ClipQuality("a", np.zeros(0), np.zeros(0), np.zeros(0))
    assert combine([empty])["a"].combined.tolist() == []
