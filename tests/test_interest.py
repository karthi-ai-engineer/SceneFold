"""What was happening, and which angle was pointed at it: the numbers behind the smarter cut."""

import numpy as np

from scenefold.interest import (
    SPREAD_S,
    WITNESSED,
    Interest,
    cutting_cost,
    measure,
)
from scenefold.knowledge import Event, Evidence


def _event(t_master_s, saw, strength=1.0, kind="sound"):
    return Event(
        event_id=f"e-{t_master_s}",
        t_master_s=t_master_s,
        kind=kind,
        strength=strength,
        recording=3,
        evidence=[
            Evidence(clip_id=c, t_local_s=t_master_s, kind=kind, strength=strength) for c in saw
        ],
    )


def test_a_second_nothing_happened_in_is_worth_nothing_extra():
    found = measure([_event(20.0, ["a"])], start_s=0.0, seconds=60, clip_ids=["a", "b"])
    assert found.happening[0] == 0.0
    assert found.worth("a")[0] == 0.0
    assert found.worth("b")[0] == 0.0


def test_the_angle_that_caught_the_moment_is_worth_more_than_the_one_that_missed_it():
    found = measure([_event(20.0, ["a"])], start_s=0.0, seconds=60, clip_ids=["a", "b"])
    assert found.worth("a")[20] > found.worth("b")[20]
    assert found.worth("b")[20] == 0.0
    # and it can never be worth more than the weight the picture is judged on
    assert found.worth("a").max() <= WITNESSED


def test_a_moment_carries_over_the_seconds_around_it():
    found = measure([_event(30.0, ["a"])], start_s=0.0, seconds=60, clip_ids=["a"])
    assert found.happening[30] > found.happening[29] > found.happening[28]
    assert found.happening[int(30 - SPREAD_S)] == 0.0


def test_a_moment_four_phones_caught_outweighs_one_phones_shake():
    crowded = measure(
        [_event(10.0, ["a", "b", "c", "d"]), _event(40.0, ["a"])],
        start_s=0.0,
        seconds=60,
        clip_ids=["a", "b", "c", "d"],
    )
    assert crowded.happening[10] > crowded.happening[40]


def test_a_faint_moment_counts_for_less_than_a_strong_one():
    found = measure(
        [_event(10.0, ["a"], strength=1.0), _event(40.0, ["a"], strength=0.3)],
        start_s=0.0,
        seconds=60,
        clip_ids=["a"],
    )
    assert found.happening[10] > found.happening[40]


def test_what_share_of_a_moment_each_angle_saw():
    # two moments in the same second: a saw both, b saw one, c saw neither
    found = measure(
        [_event(10.0, ["a", "b"]), _event(10.0, ["a"])],
        start_s=0.0,
        seconds=30,
        clip_ids=["a", "b", "c"],
    )
    assert found.saw["a"][10] == 1.0
    assert 0.0 < found.saw["b"][10] < 1.0
    assert found.saw["c"][10] == 0.0


def test_the_film_starting_late_does_not_shift_what_happened():
    late = measure([_event(130.0, ["a"])], start_s=100.0, seconds=60, clip_ids=["a"])
    assert late.happening.argmax() == 30


def test_moments_outside_the_film_are_left_out():
    found = measure(
        [_event(-50.0, ["a"]), _event(20.0, ["a"]), _event(500.0, ["a"])],
        start_s=0.0,
        seconds=60,
        clip_ids=["a"],
    )
    assert found.events == 1


def test_an_event_with_no_moments_in_it_decides_nothing():
    found = measure([], start_s=0.0, seconds=60, clip_ids=["a"])
    assert not found.known
    assert not found.worth("a").any()


def test_an_angle_the_store_never_heard_of_is_worth_nothing_extra():
    found = measure([_event(20.0, ["a"])], start_s=0.0, seconds=60, clip_ids=["a"])
    assert not found.worth("a-stranger").any()
    assert len(found.worth("a-stranger")) == 60


def test_cutting_across_a_moment_costs_more_than_cutting_on_the_quiet():
    found = measure([_event(30.0, ["a", "b"])], start_s=0.0, seconds=60, clip_ids=["a", "b"])
    across = cutting_cost(found, 30, 0.35)
    quiet = cutting_cost(found, 5, 0.35)
    assert across > quiet
    assert quiet == 0.35  # nothing happening, so nothing added


def test_a_cut_at_the_very_start_or_end_costs_the_usual():
    found = measure([_event(0.0, ["a"])], start_s=0.0, seconds=10, clip_ids=["a"])
    assert cutting_cost(found, 0, 0.35) == 0.35
    assert cutting_cost(found, 10, 0.35) == 0.35


def test_the_loudest_moment_cannot_run_away_with_the_film():
    """A strength arrives already scaled against its own clip, so one bang stays in proportion.

    A door slamming by the microphone is the strongest thing that clip heard, so its strength is
    1.0 — the same ceiling as the moment everyone came for. What separates them is how many phones
    caught it, which is the thing worth separating them by.
    """
    ordinary = [_event(float(t), ["a", "b"]) for t in range(10, 100, 10)]
    slam = _event(55.0, ["a"], strength=1.0)  # loudest in its own clip, and nobody else noticed
    found = measure([*ordinary, slam], start_s=0.0, seconds=120, clip_ids=["a", "b", "c", "d"])
    assert found.happening[10] > found.happening[55]  # two phones beat one, however loud
    assert found.happening.max() <= 1.0


def test_worth_is_the_shape_of_the_film_however_the_store_is_shaped():
    found = Interest(np.zeros(45), {"a": np.zeros(45)}, events=0)
    assert found.worth("a").shape == (45,)
    assert found.worth("nobody").shape == (45,)
