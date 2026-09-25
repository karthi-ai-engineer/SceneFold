"""Placing the phones on a map from when each of them heard each sound.

The arrivals here are made the way sync hands them over: lined up by sound, so every clip already
sits ahead by its own travel time from the loudest thing in the room (the stage below). Where that
head start is known it comes in as `from_main_sound_m`; where it is not, the solver works it out.
"""

import numpy as np
import pytest

from scenefold import positions
from scenefold.manifest import now
from scenefold.positions import (
    SOUND_M_PER_S,
    CameraMap,
    MapError,
    SoundEvent,
    _align_points,
    load_positions,
    map_event,
    metres,
    solve_map,
)
from scenefold.timeline import ClipPlacement, SyncSettings, Timeline, save_timeline

CAMERAS = np.array([[0.0, 0.0], [12.0, 1.0], [6.0, 9.0], [-5.0, 6.0], [3.0, -8.0]])
SIX = np.vstack([CAMERAS, [-12.0, -4.0]])
SOUNDS = np.array(
    [[0.0, 25.0], [-18.0, -4.0], [20.0, -10.0], [-9.0, 14.0], [14.0, 18.0], [0.0, -20.0]]
)
STAGE = np.array([0.0, 40.0])  # the loudest thing, the one sync lined the clips up on


def clip_ids(count: int) -> list[str]:
    return [f"cam{i}" for i in range(count)]


def head_start_m(cameras: np.ndarray) -> dict[str, float]:
    """How much further from the stage each clip stood than the nearest: sync's circle answer."""
    far = np.linalg.norm(cameras - STAGE, axis=1)
    return {clip_id: float(far[i] - far.min()) for i, clip_id in enumerate(clip_ids(len(cameras)))}


def heard(
    cameras: np.ndarray,
    sounds: np.ndarray,
    *,
    noise_ms: float = 0.0,
    only: dict[int, list[int]] | None = None,
    seed: int = 7,
) -> list[SoundEvent]:
    """The arrivals such an arrangement would produce, in sync's sound-lined-up time.

    `only` limits which cameras (by index) caught a given sound, for the partly heard cases.
    """
    rng = np.random.default_rng(seed)
    ids = clip_ids(len(cameras))
    ahead = head_start_m(cameras)
    events = []
    for j, sound in enumerate(sounds):
        made = 30.0 * j
        listeners = only.get(j, list(range(len(cameras)))) if only else range(len(cameras))
        arrivals = {}
        for i in listeners:
            travel = float(np.linalg.norm(cameras[i] - sound)) / SOUND_M_PER_S
            slip = rng.normal(0, noise_ms / 1000) if noise_ms else 0.0
            arrivals[ids[i]] = made + travel - ahead[ids[i]] / SOUND_M_PER_S + slip
        events.append(
            SoundEvent(
                label=f"clap {j}",
                arrivals_s=arrivals,
                confidence=9.0,
                spread_s=max(arrivals.values()) - min(arrivals.values()),
            )
        )
    return events


def names(count: int) -> dict[str, str]:
    return {clip_id: f"{clip_id}.mp4" for clip_id in clip_ids(count)}


def errors_m(camera_map: CameraMap, truth: np.ndarray) -> dict[int, float]:
    """How far each placed clip sits from the truth, once slid, turned, and mirrored onto it."""
    kept = [i for i, place in enumerate(camera_map.cameras) if place.placed]
    found = np.array([[camera_map.cameras[i].x_m, camera_map.cameras[i].y_m] for i in kept])
    fitted = _align_points(found, truth[kept])
    return dict(zip(kept, np.linalg.norm(fitted - truth[kept], axis=1), strict=True))


# --- the helper the accuracy checks lean on


def test_aligning_undoes_a_slide_a_turn_and_a_mirror():
    angle = 0.7
    turn = np.array([[np.cos(angle), np.sin(angle)], [-np.sin(angle), np.cos(angle)]])
    moved = (CAMERAS * np.array([1.0, -1.0])) @ turn + np.array([31.0, -12.0])
    assert np.allclose(_align_points(moved, CAMERAS), CAMERAS, atol=1e-9)


# --- placing the cameras, with the head start already measured


def test_recovers_a_known_arrangement():
    camera_map = solve_map(heard(CAMERAS, SOUNDS), names(5), head_start_m(CAMERAS))
    assert camera_map.solved
    assert all(place.placed for place in camera_map.cameras)
    assert camera_map.events_used == len(SOUNDS)
    assert camera_map.head_start_from_pictures
    assert max(errors_m(camera_map, CAMERAS).values()) < 0.5
    assert camera_map.residual_ms is not None and camera_map.residual_ms < 0.1
    assert camera_map.mirror_ambiguous  # sound can never tell a map from its mirror image


def test_survives_a_few_milliseconds_of_noise():
    events = heard(CAMERAS, SOUNDS, noise_ms=2.0)
    camera_map = solve_map(events, names(5), head_start_m(CAMERAS))
    assert camera_map.solved
    assert max(errors_m(camera_map, CAMERAS).values()) < 4.0
    assert all(place.uncertainty_m is not None for place in camera_map.cameras)


def test_says_how_much_each_place_rests_on_one_sound():
    circles = head_start_m(CAMERAS)
    clean = solve_map(heard(CAMERAS, SOUNDS), names(5), circles)
    noisy = solve_map(heard(CAMERAS, SOUNDS, noise_ms=5.0, seed=3), names(5), circles)
    steady = max(place.uncertainty_m for place in clean.cameras)
    shaky = max(place.uncertainty_m for place in noisy.cameras)
    assert steady < shaky  # noisier arrivals must not come out looking as certain


def test_cameras_in_a_straight_line_still_place():
    line = np.array([[0.0, 0.0], [8.0, 0.0], [16.0, 0.0], [24.0, 0.0], [32.0, 0.0]])
    camera_map = solve_map(heard(line, SOUNDS), names(5), head_start_m(line))
    assert camera_map.solved
    assert max(errors_m(camera_map, line).values()) < 1.0


def test_one_wrong_arrival_does_not_bend_the_map():
    events = heard(CAMERAS, SOUNDS)
    events[2].arrivals_s["cam3"] += 0.2  # 68 m out: one sound mistaken for another
    camera_map = solve_map(events, names(5), head_start_m(CAMERAS))
    assert camera_map.solved
    errors = errors_m(camera_map, CAMERAS)
    assert max(error for i, error in errors.items() if i != 3) < 4.0


# --- working the head start out as well


def test_works_out_the_head_start_when_the_pictures_never_measured_it():
    camera_map = solve_map(heard(CAMERAS, SOUNDS), names(5))  # no circle answer to lean on
    assert camera_map.solved, camera_map.reason
    assert not camera_map.head_start_from_pictures
    assert max(errors_m(camera_map, CAMERAS).values()) < 1.0


def test_without_the_pictures_five_sounds_are_not_enough():
    camera_map = solve_map(heard(CAMERAS, SOUNDS[:5]), names(5))
    assert not camera_map.solved
    assert "sync never measured how far each clip stood" in camera_map.reason


def test_a_missing_circle_answer_is_enough_to_need_the_harder_solve():
    circles = head_start_m(CAMERAS)
    circles["cam2"] = None  # the picture pass could not tell for one clip
    camera_map = solve_map(heard(CAMERAS, SOUNDS[:5]), names(5), circles)
    assert not camera_map.solved and not camera_map.head_start_from_pictures


# --- refusing, and saying why


def test_refuses_when_there_are_too_few_sounds():
    camera_map = solve_map(heard(CAMERAS[:4], SOUNDS[:2]), names(4), head_start_m(CAMERAS[:4]))
    assert not camera_map.solved
    assert "measurements" in camera_map.reason
    assert all(not place.placed and place.reason for place in camera_map.cameras)


def test_five_cameras_and_five_sounds_are_enough():
    camera_map = solve_map(heard(CAMERAS, SOUNDS[:5]), names(5), head_start_m(CAMERAS))
    assert camera_map.solved, camera_map.reason


def test_refuses_when_every_sound_came_from_one_place():
    stage = np.repeat(np.array([[0.0, 30.0]]), 5, axis=0)
    circles = head_start_m(CAMERAS)
    camera_map = solve_map(heard(CAMERAS, stage), names(5), circles)
    assert not camera_map.solved
    assert "circle" in camera_map.reason
    # the circle answer sync already measured survives a refusal
    kept = [place.from_main_sound_m for place in camera_map.cameras]
    assert kept == [round(circles[clip_id], 1) for clip_id in clip_ids(5)]


def test_a_camera_that_heard_too_few_sounds_is_left_out():
    only = {j: [0, 1, 2, 3, 4] for j in range(len(SOUNDS))}
    only[0] = [0, 1, 2, 3, 4, 5]  # the sixth phone caught one sound only
    camera_map = solve_map(heard(SIX, SOUNDS, only=only), names(6), head_start_m(SIX))
    assert camera_map.solved
    quiet = camera_map.cameras[5]
    assert not quiet.placed
    assert "heard only 1 of the 6 sounds" in quiet.reason
    assert all(place.placed for place in camera_map.cameras[:5])


def test_refuses_arrivals_no_arrangement_explains():
    rng = np.random.default_rng(11)
    events = [
        SoundEvent(
            label=f"noise {j}",
            arrivals_s={clip_id: 20.0 * j + float(rng.uniform(0, 0.25)) for clip_id in clip_ids(6)},
            confidence=5.0,
            spread_s=0.25,
        )
        for j in range(8)
    ]
    camera_map = solve_map(events, names(6), {clip_id: 0.0 for clip_id in clip_ids(6)})
    # either the arrivals fit nothing, or nothing can be pinned down; both are honest refusals
    assert not camera_map.solved
    assert not any(place.placed for place in camera_map.cameras)


def test_refuses_when_no_sound_reached_three_clips():
    events = [
        SoundEvent(label="a", arrivals_s={"cam0": 1.0, "cam1": 1.02}, confidence=9.0, spread_s=0.02)
    ]
    camera_map = solve_map(events, names(3))
    assert not camera_map.solved
    assert "nothing to place them by" in camera_map.reason


def test_refuses_when_only_two_clips_heard_the_sounds():
    # two clips hear everything; the other three take turns making up the third listener
    only = {j: [0, 1, 2 + j % 3] for j in range(len(SOUNDS))}
    camera_map = solve_map(heard(CAMERAS, SOUNDS, only=only), names(5), head_start_m(CAMERAS))
    assert not camera_map.solved
    assert "a map needs 3" in camera_map.reason


# --- the same answer every time, and on disk


def test_same_arrivals_give_the_same_map():
    events = heard(CAMERAS, SOUNDS, noise_ms=2.0)
    circles = head_start_m(CAMERAS)
    first = solve_map(events, names(5), circles)
    second = solve_map(events, names(5), circles)
    assert first.model_dump(exclude={"created_at"}) == second.model_dump(exclude={"created_at"})


def test_map_event_reads_the_timeline_and_writes_the_map(tmp_path, monkeypatch):
    event_dir = tmp_path / "party"
    event_dir.mkdir()
    ahead = head_start_m(CAMERAS)
    save_timeline(
        event_dir,
        Timeline(
            event_id="party",
            created_at=now(),
            settings=SyncSettings(),
            duration_s=200.0,
            clips=[
                ClipPlacement(
                    clip_id=clip_id,
                    name=f"{clip_id}.mp4",
                    placed=True,
                    offset_s=0.0,
                    duration_s=200.0,
                    heard_late_s=ahead[clip_id] / SOUND_M_PER_S,
                )
                for clip_id in clip_ids(5)
            ],
            pairs=[],
        ),
    )
    monkeypatch.setattr(positions, "_load_events", lambda *_: heard(CAMERAS, SOUNDS))

    camera_map = map_event("party", data_dir=tmp_path)
    assert camera_map.solved and camera_map.event_id == "party"
    assert camera_map.head_start_from_pictures
    assert max(errors_m(camera_map, CAMERAS).values()) < 0.5
    # sync's circle answer is carried through, in metres
    assert camera_map.cameras[1].from_main_sound_m == round(
        metres(ahead["cam1"] / SOUND_M_PER_S), 1
    )
    assert load_positions(event_dir).model_dump() == camera_map.model_dump()


def test_map_event_needs_a_synced_event(tmp_path):
    (tmp_path / "party").mkdir()
    with pytest.raises(MapError, match="run `scenefold sync` first"):
        map_event("party", data_dir=tmp_path)


def test_map_event_refuses_with_too_few_placed_clips(tmp_path, monkeypatch):
    event_dir = tmp_path / "party"
    event_dir.mkdir()
    save_timeline(
        event_dir,
        Timeline(
            event_id="party",
            created_at=now(),
            settings=SyncSettings(),
            duration_s=10.0,
            clips=[
                ClipPlacement(
                    clip_id="cam0", name="cam0.mp4", placed=True, offset_s=0.0, duration_s=10.0
                ),
                ClipPlacement(clip_id="cam1", name="cam1.mp4", placed=False, duration_s=10.0),
            ],
            pairs=[],
        ),
    )
    monkeypatch.setattr(positions, "_load_events", lambda *_: [])
    camera_map = map_event("party", data_dir=tmp_path)
    assert not camera_map.solved
    assert "a map needs 3" in camera_map.reason
    assert load_positions(event_dir) is not None  # written even when it cannot answer
