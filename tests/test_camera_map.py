"""End to end: real audio of known geometry, through the sounds, into a map of where phones stood.

`tests/test_sound_events.py` checks the arrivals and `tests/test_positions.py` the solver on exact
numbers. This file runs the whole way: phones placed on a field hear claps made at known spots, and
the map that comes out is compared with where they really were.
"""

from datetime import UTC, datetime

import numpy as np
import pytest
from synth import claps, heard_at, scene, write_wav

from scenefold.cli import main
from scenefold.positions import POSITIONS_NAME, load_positions, map_event, solve_map
from scenefold.sound_events import find_sound_events
from scenefold.timeline import ClipPlacement, SyncSettings, Timeline, save_timeline

SOUND_M_PER_S = 343.0
SECONDS = 24.0
# Five phones around a small field, and claps made at six different places on it.
CAMERAS = {
    "aaaaaaaaaaaa": (0.0, 0.0),
    "bbbbbbbbbbbb": (30.0, 4.0),
    "cccccccccccc": (26.0, 22.0),
    "dddddddddddd": (2.0, 18.0),
    "eeeeeeeeeeee": (14.0, 30.0),
}
CLAP_SPOTS = [(15.0, -6.0), (-8.0, 12.0), (34.0, 14.0), (12.0, 26.0), (5.0, 2.0), (28.0, 30.0)]
CLAP_TIMES = (2.5, 6.0, 9.5, 13.0, 16.5, 20.0)


def align(found: np.ndarray, truth: np.ndarray) -> np.ndarray:
    """Slide, turn and if need be mirror `found` onto `truth`: a map's frame carries no meaning."""
    a = found - found.mean(axis=0)
    b = truth - truth.mean(axis=0)
    scale = np.linalg.norm(b) / max(np.linalg.norm(a), 1e-9)
    u, _, vt = np.linalg.svd(a.T @ b)  # includes the mirror, which sound can never resolve
    return (a * scale) @ (u @ vt) + truth.mean(axis=0)


def build_event(tmp_path, cameras=CAMERAS, spots=CLAP_SPOTS, single_spot=False):
    """Write one WAV per phone: the background of the place, plus claps made at known spots."""
    event_dir = tmp_path / "field"
    (event_dir / "proxies").mkdir(parents=True)
    background = scene(SECONDS, seed=7)
    places = [spots[0]] * len(CLAP_TIMES) if single_spot else spots
    # The room itself: quiet enough that the claps below stand out, as a clap in a real room does
    # (`scene` has transients of its own, so it is kept low). When every sound is to come from one
    # place, the room sits there too — otherwise it is a second place, and two places can be solved.
    room = places[0] if single_spot else (15.0, 10.0)
    sources = [(room, background * 0.08)]
    sources += [
        (place, claps(SECONDS, (when,))) for place, when in zip(places, CLAP_TIMES, strict=False)
    ]

    # Sync lines clips up by what they HEARD, so each clip's offset absorbs its own travel time
    # from the loudest source (the room, in the middle). That is the bias the solver has to undo,
    # and `heard_late_s` — counted from the nearest phone, as sync writes it — is how it undoes it.
    biases = {
        clip_id: float(np.hypot(*(np.array(position) - np.array(room)))) / SOUND_M_PER_S
        for clip_id, position in cameras.items()
    }
    nearest = min(biases.values())
    placements, heard_late = [], {}
    for index, (clip_id, position) in enumerate(cameras.items()):
        write_wav(event_dir / "proxies" / f"{clip_id}.wav", heard_at(position, sources, seed=index))
        heard_late[clip_id] = biases[clip_id] - nearest
        placements.append(
            ClipPlacement(
                clip_id=clip_id,
                name=f"phone_{index}.mp4",
                placed=True,
                offset_s=-biases[clip_id],
                duration_s=SECONDS,
                heard_late_s=heard_late[clip_id],
            )
        )
    timeline = Timeline(
        event_id="field",
        created_at=datetime.now(UTC),
        settings=SyncSettings(),
        duration_s=SECONDS,
        clips=placements,
        pairs=[],
    )
    save_timeline(event_dir, timeline)
    truth = np.array(list(cameras.values()), dtype=float)
    return event_dir, timeline, truth, heard_late


def solved_map(event_dir, timeline, heard_late):
    events = find_sound_events(event_dir, timeline)
    clips = {clip.clip_id: clip.name for clip in timeline.clips}
    circles = {clip_id: late * SOUND_M_PER_S for clip_id, late in heard_late.items()}
    return events, solve_map(events, clips, from_main_sound_m=circles)


def positions_of(camera_map, order):
    return np.array(
        [[c.x_m, c.y_m] for c in sorted(camera_map.cameras, key=lambda c: order.index(c.clip_id))]
    )


def test_the_map_puts_the_phones_where_they_stood(tmp_path):
    event_dir, timeline, truth, heard_late = build_event(tmp_path)
    events, camera_map = solved_map(event_dir, timeline, heard_late)
    assert len(events) >= 5, f"only {len(events)} sounds were found"
    assert camera_map.solved, camera_map.reason
    assert all(c.placed for c in camera_map.cameras), [c.reason for c in camera_map.cameras]

    found = align(positions_of(camera_map, list(CAMERAS)), truth)
    error = np.linalg.norm(found - truth, axis=1)
    assert error.max() < 3.0, f"worst phone is {error.max():.1f} m out ({error.round(1)})"


def test_a_map_never_claims_to_know_which_way_round_it_is(tmp_path):
    event_dir, timeline, _, heard_late = build_event(tmp_path)
    _, camera_map = solved_map(event_dir, timeline, heard_late)
    assert camera_map.mirror_ambiguous


def test_sounds_from_one_place_give_distances_not_a_map(tmp_path):
    event_dir, timeline, _, heard_late = build_event(tmp_path, single_spot=True)
    _, camera_map = solved_map(event_dir, timeline, heard_late)
    assert not camera_map.solved
    assert camera_map.reason
    assert any(c.from_main_sound_m is not None for c in camera_map.cameras)


def test_map_event_writes_the_map_and_the_cli_prints_it(tmp_path, capsys):
    data_dir = tmp_path / "data"
    event_dir, *_ = build_event(data_dir)
    written = map_event("field", data_dir=data_dir)
    assert (event_dir / POSITIONS_NAME).is_file()
    assert load_positions(event_dir) == written

    assert main(["map", "field", "--data-dir", str(data_dir)]) == 0
    out = capsys.readouterr().out
    assert "field" in out and POSITIONS_NAME in out
    if written.solved:
        assert "mirrored" in out or "turned" in out


def test_a_missing_event_is_refused(tmp_path, capsys):
    assert main(["map", "nothing-here", "--data-dir", str(tmp_path)]) == 2
    assert "error:" in capsys.readouterr().err


@pytest.mark.parametrize("keep", [3, 4])
def test_too_few_phones_or_sounds_refuse_rather_than_guess(tmp_path, keep):
    cameras = dict(list(CAMERAS.items())[:keep])
    event_dir, timeline, _, heard_late = build_event(
        tmp_path, cameras=cameras, spots=CLAP_SPOTS[:2]
    )
    _, camera_map = solved_map(event_dir, timeline, heard_late)
    assert not camera_map.solved
    assert camera_map.reason
