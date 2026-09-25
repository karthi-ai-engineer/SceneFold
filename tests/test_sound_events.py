"""Finding one sound in several clips, and timing when each of them heard it.

The clips here are built from known geometry: cameras and claps are put at metre positions, and
each clip gets each clap delayed by its own distance at 343 m/s. The arrival differences the code
recovers are then checked against the distance differences that made them.
"""

from datetime import UTC, datetime

import numpy as np
import pytest
from synth import write_wav

from scenefold.positions import SOUND_M_PER_S
from scenefold.sound_events import find_sound_events, onsets
from scenefold.timeline import ClipPlacement, SyncSettings, Timeline

RATE = 48_000
SECONDS = 24.0

# One event: four phones around a square, and claps made at three different spots.
CAMERAS = {
    "cam_a": (0.0, 0.0),
    "cam_b": (40.0, 0.0),
    "cam_c": (40.0, 30.0),
    "cam_d": (0.0, 30.0),
}
CLAPS = [(20.0, 15.0, 4.0), (0.0, 5.0, 9.5), (38.0, 28.0, 15.0), (10.0, 25.0, 19.0)]


def distance(camera: tuple[float, float], clap: tuple[float, float, float]) -> float:
    return float(np.hypot(camera[0] - clap[0], camera[1] - clap[1]))


def clap_sound(rate: int, seed: int = 0) -> np.ndarray:
    """A short, sharp burst: loud attack, quick decay, like hands or a hit."""
    rng = np.random.default_rng(seed)
    length = int(0.04 * rate)
    time = np.arange(length) / rate
    return (rng.standard_normal(length) * np.exp(-time * 90)).astype(np.float32)


def build_clip(
    camera: tuple[float, float],
    *,
    start_s: float,
    claps: list[tuple[float, float, float]] = CLAPS,
    seconds: float = SECONDS,
    rate: int = RATE,
    gain: float = 1.0,
    noise: float = 0.01,
    drift_ppm: float = 0.0,
    echo: float = 0.0,
    seed: int = 1,
) -> np.ndarray:
    """What one phone recorded: room noise, plus every clap delayed by how far away it was made."""
    rng = np.random.default_rng(seed)
    samples = rng.standard_normal(int(seconds * rate)).astype(np.float32) * noise
    for index, (x, y, made_at) in enumerate(claps):
        burst = clap_sound(rate, index)  # one clap is the same sound at every phone
        heard_at_master = made_at + distance(camera, (x, y, made_at)) / SOUND_M_PER_S
        local = (heard_at_master - start_s) * (1 + drift_ppm * 1e-6)  # the phone's own clock
        at = round(local * rate)
        if 0 <= at < len(samples) - len(burst):
            samples[at : at + len(burst)] += burst * gain
    if echo:
        impulse = np.zeros(int(0.08 * rate), dtype=np.float32)
        impulse[0] = 1.0
        for delay_ms in (17, 31, 53):
            impulse[int(delay_ms * rate / 1000)] = echo * rng.uniform(0.3, 1.0)
        samples = np.convolve(samples, impulse)[: len(samples)].astype(np.float32)
    return samples


def workspace(
    tmp_path,
    clips: dict[str, np.ndarray],
    *,
    starts: dict[str, float] | None = None,
    drifts: dict[str, float] | None = None,
):
    """An event folder holding these clips' sound, and a timeline placing them by their starts."""
    proxies = tmp_path / "proxies"
    proxies.mkdir(parents=True, exist_ok=True)
    starts = starts or dict.fromkeys(clips, 0.0)
    drifts = drifts or {}
    placements = []
    for clip_id, samples in clips.items():
        write_wav(proxies / f"{clip_id}.wav", samples, RATE)
        placements.append(
            ClipPlacement(
                clip_id=clip_id,
                name=f"{clip_id}.mp4",
                placed=True,
                offset_s=starts[clip_id],
                drift_ppm=drifts.get(clip_id),
                duration_s=len(samples) / RATE,
                confidence=10.0,
            )
        )
    timeline = Timeline(
        event_id="map-test",
        created_at=datetime.now(UTC),
        settings=SyncSettings(),
        duration_s=SECONDS,
        clips=placements,
        pairs=[],
    )
    return tmp_path, timeline


def true_difference(clip_a: str, clip_b: str, clap: tuple[float, float, float]) -> float:
    """How much later clip_b heard this clap than clip_a did, from the geometry alone."""
    return (distance(CAMERAS[clip_b], clap) - distance(CAMERAS[clip_a], clap)) / SOUND_M_PER_S


def worst_error(events, claps=CLAPS) -> float:
    """The largest error, in seconds, between measured arrival differences and the true ones."""
    worst = 0.0
    for event in events:
        clap = min(claps, key=lambda c: abs(min(event.arrivals_s.values()) - c[2]))
        clips = sorted(event.arrivals_s)
        for other in clips[1:]:
            measured = event.arrivals_s[other] - event.arrivals_s[clips[0]]
            worst = max(worst, abs(measured - true_difference(clips[0], other, clap)))
    return worst


# --- finding the sounds


def test_a_clap_is_found_where_it_was_made(tmp_path):
    clips = {
        name: build_clip(xy, start_s=0.0, seed=i) for i, (name, xy) in enumerate(CAMERAS.items())
    }
    event_dir, timeline = workspace(tmp_path, clips)

    events = find_sound_events(event_dir, timeline)

    assert len(events) >= 3, "the claps should be found"
    assert all(len(e.arrivals_s) == 4 for e in events), "every phone heard every clap"
    assert worst_error(events) < 0.001, "arrival differences should match the distances"


def test_arrival_differences_are_the_distance_differences(tmp_path):
    clips = {
        name: build_clip(xy, start_s=0.0, seed=i) for i, (name, xy) in enumerate(CAMERAS.items())
    }
    event_dir, timeline = workspace(tmp_path, clips)

    events = find_sound_events(event_dir, timeline)

    # cam_a and cam_c sit across the diagonal, so the same clap reaches them metres apart in time
    for event in events:
        if {"cam_a", "cam_c"} <= set(event.arrivals_s):
            gap_m = abs(event.arrivals_s["cam_c"] - event.arrivals_s["cam_a"]) * SOUND_M_PER_S
            assert gap_m < 60.0, "no two of these phones are further apart than the field"
    assert worst_error(events) * SOUND_M_PER_S < 0.5, "within half a metre of the truth"


def test_clips_that_start_at_different_times(tmp_path):
    starts = {"cam_a": 0.0, "cam_b": 2.5, "cam_c": 1.0, "cam_d": 3.25}
    clips = {
        name: build_clip(xy, start_s=starts[name], seconds=SECONDS - starts[name], seed=i)
        for i, (name, xy) in enumerate(CAMERAS.items())
    }
    event_dir, timeline = workspace(tmp_path, clips, starts=starts)

    events = find_sound_events(event_dir, timeline)

    assert len(events) >= 2
    assert worst_error(events) < 0.001


def test_clock_drift_does_not_bias_the_arrivals(tmp_path):
    drifts = {"cam_b": 500.0, "cam_d": -300.0}
    clips = {
        name: build_clip(xy, start_s=0.0, drift_ppm=drifts.get(name, 0.0), seed=i)
        for i, (name, xy) in enumerate(CAMERAS.items())
    }
    event_dir, timeline = workspace(tmp_path, clips, drifts=drifts)

    events = find_sound_events(event_dir, timeline)

    assert len(events) >= 3
    assert worst_error(events) < 0.001, "the master-time conversion should cancel drift"


def test_noise_gain_and_echo_do_not_break_it(tmp_path):
    settings = {
        "cam_a": {"gain": 1.0, "noise": 0.02, "echo": 0.0},
        "cam_b": {"gain": 0.35, "noise": 0.05, "echo": 0.4},
        "cam_c": {"gain": 0.6, "noise": 0.03, "echo": 0.25},
        "cam_d": {"gain": 0.2, "noise": 0.04, "echo": 0.5},
    }
    clips = {
        name: build_clip(xy, start_s=0.0, seed=i, **settings[name])
        for i, (name, xy) in enumerate(CAMERAS.items())
    }
    event_dir, timeline = workspace(tmp_path, clips)

    events = find_sound_events(event_dir, timeline)

    assert len(events) >= 2
    assert worst_error(events) < 0.003, "echo and noise cost a fraction of a millisecond here"


# --- refusing what it cannot know


def test_a_sound_only_two_clips_heard_is_not_returned(tmp_path):
    lonely = (100.0, 100.0, 12.0)  # made far away; only two phones get it in their audio
    clips = {
        "cam_a": build_clip(CAMERAS["cam_a"], start_s=0.0, claps=[lonely], seed=0),
        "cam_b": build_clip(CAMERAS["cam_b"], start_s=0.0, claps=[lonely], seed=1),
        "cam_c": build_clip(CAMERAS["cam_c"], start_s=0.0, claps=[], seed=2),
        "cam_d": build_clip(CAMERAS["cam_d"], start_s=0.0, claps=[], seed=3),
    }
    event_dir, timeline = workspace(tmp_path, clips)

    events = find_sound_events(event_dir, timeline)

    assert events == [], "two clips cannot make an event"


def test_a_clip_of_unrelated_sound_takes_no_part(tmp_path):
    clips = {
        name: build_clip(xy, start_s=0.0, seed=i) for i, (name, xy) in enumerate(CAMERAS.items())
    }
    rng = np.random.default_rng(99)
    clips["passer_by"] = (rng.standard_normal(int(SECONDS * RATE)) * 0.05).astype(np.float32)
    event_dir, timeline = workspace(tmp_path, clips)

    events = find_sound_events(event_dir, timeline)

    assert events, "the real claps are still found"
    assert all("passer_by" not in e.arrivals_s for e in events), "noise is not an arrival"


def test_too_few_clips_gives_nothing(tmp_path):
    clips = {
        name: build_clip(CAMERAS[name], start_s=0.0, seed=i)
        for i, name in enumerate(["cam_a", "cam_b"])
    }
    event_dir, timeline = workspace(tmp_path, clips)

    assert find_sound_events(event_dir, timeline) == []


def test_silence_gives_nothing(tmp_path):
    clips = {name: np.zeros(int(SECONDS * RATE), dtype=np.float32) for name in CAMERAS}
    event_dir, timeline = workspace(tmp_path, clips)

    assert find_sound_events(event_dir, timeline) == []


def test_a_clip_whose_sound_is_missing_is_skipped(tmp_path):
    clips = {
        name: build_clip(xy, start_s=0.0, seed=i) for i, (name, xy) in enumerate(CAMERAS.items())
    }
    event_dir, timeline = workspace(tmp_path, clips)
    (event_dir / "proxies" / "cam_d.wav").unlink()

    events = find_sound_events(event_dir, timeline)

    assert events
    assert all("cam_d" not in e.arrivals_s for e in events)


def test_one_clip_hearing_an_extra_sound_does_not_corrupt_the_event(tmp_path):
    """Somebody claps beside one phone just after the shared clap: the shared one still wins."""
    shared = [(35.0, 6.0, 6.0)]  # off-centre, so the phones really do hear it at different moments
    clips = {
        name: build_clip(xy, start_s=0.0, claps=shared, seed=i)
        for i, (name, xy) in enumerate(CAMERAS.items())
    }
    beside_cam_d = build_clip(
        CAMERAS["cam_d"], start_s=0.0, claps=[(0.0, 30.0, 6.35)], seed=7, noise=0.0
    )
    clips["cam_d"] = clips["cam_d"] + beside_cam_d

    event_dir, timeline = workspace(tmp_path, clips)
    events = find_sound_events(event_dir, timeline)

    assert len(events) == 1, "the sound only one phone heard is not an event"
    assert worst_error(events, claps=shared) < 0.001, "every arrival is the shared clap's"


# --- the shape of what comes back


def test_the_same_clips_give_the_same_events(tmp_path):
    clips = {
        name: build_clip(xy, start_s=0.0, seed=i) for i, (name, xy) in enumerate(CAMERAS.items())
    }
    event_dir, timeline = workspace(tmp_path, clips)

    first = find_sound_events(event_dir, timeline)
    second = find_sound_events(event_dir, timeline)

    assert [e.model_dump() for e in first] == [e.model_dump() for e in second]


def test_the_most_confident_events_come_first(tmp_path):
    clips = {
        name: build_clip(xy, start_s=0.0, seed=i) for i, (name, xy) in enumerate(CAMERAS.items())
    }
    event_dir, timeline = workspace(tmp_path, clips)

    events = find_sound_events(event_dir, timeline)

    assert events == sorted(events, key=lambda e: (-e.confidence, min(e.arrivals_s.values())))
    assert all(e.spread_s <= 0.6 for e in events), "an event is one sound, not several"
    assert all(e.label == "transient" for e in events)


def test_only_as_many_events_as_asked_for(tmp_path):
    clips = {
        name: build_clip(xy, start_s=0.0, seed=i) for i, (name, xy) in enumerate(CAMERAS.items())
    }
    event_dir, timeline = workspace(tmp_path, clips)

    events = find_sound_events(event_dir, timeline, max_events=2)

    assert len(events) <= 2


# --- the onset detector on its own


def test_onsets_find_a_clap_where_it_happened():
    rate = 16_000
    rng = np.random.default_rng(3)
    samples = (rng.standard_normal(int(10 * rate)) * 0.01).astype(np.float32)
    burst = clap_sound(rate, 5)
    for at in (2.0, 5.5, 8.0):
        index = round(at * rate)
        samples[index : index + len(burst)] += burst

    times, rises = onsets(samples, rate)

    assert len(times) == 3
    assert times == pytest.approx([2.0, 5.5, 8.0], abs=0.02)
    assert all(rises > 6.0)


def test_onsets_ignore_a_slow_swell():
    rate = 16_000
    rng = np.random.default_rng(4)
    swell = np.linspace(0.01, 0.5, int(10 * rate))
    samples = (rng.standard_normal(int(10 * rate)) * swell).astype(np.float32)

    times, _ = onsets(samples, rate)

    assert len(times) == 0, "a gradual rise is not a moment anything can be timed against"
