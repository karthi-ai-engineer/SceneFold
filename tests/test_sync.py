"""Tests for solving the master timeline and for `scenefold sync`."""

from pathlib import Path

import numpy as np
import pytest
import synth
from conftest import needs_ffmpeg, run_ffmpeg

from scenefold.audio_offset import measure_offset
from scenefold.cli import main
from scenefold.ingest import ingest
from scenefold.sync import NOT_MATCHED, SyncError, solve_timeline, sync_event
from scenefold.timeline import TIMELINE_NAME, PairMeasurement, SyncSettings, load_timeline

SETTINGS = SyncSettings()


def pair(a: str, b: str, lag: float | None, confidence: float | None = 10.0) -> PairMeasurement:
    return PairMeasurement(
        clip_a=a,
        clip_b=b,
        lag_s=lag,
        confidence=confidence,
        overlap_s=None if lag is None else 20.0,
    )


def by_clips(pairs: list[PairMeasurement]) -> dict[tuple[str, str], PairMeasurement]:
    return {(p.clip_a, p.clip_b): p for p in pairs}


# --- solving offsets (pure)


def test_clips_that_never_overlap_are_placed_through_others():
    offsets, _, pairs = solve_timeline(
        ["A", "B", "C"], [pair("A", "B", 5.0), pair("B", "C", 7.0)], SETTINGS
    )
    assert offsets == pytest.approx({"A": 0.0, "B": 5.0, "C": 12.0})
    assert all(p.used and p.rejected is None for p in pairs)


def test_one_wrong_pair_is_dropped_as_inconsistent():
    truth = {"A": 0.0, "B": 3.0, "C": 6.0, "D": 9.0}
    measured = []
    for a, b in [("A", "B"), ("A", "C"), ("A", "D"), ("B", "C"), ("B", "D"), ("C", "D")]:
        lag = truth[b] - truth[a] + (0.5 if (a, b) == ("A", "D") else 0.0)
        measured.append(pair(a, b, lag))
    offsets, _, pairs = solve_timeline(list(truth), measured, SETTINGS)
    assert offsets == pytest.approx(truth, abs=1e-9)
    wrong = by_clips(pairs)[("A", "D")]
    assert (wrong.used, wrong.rejected) == (False, "inconsistent")
    assert sum(p.used for p in pairs) == 5


def test_small_disagreements_are_averaged_by_confidence():
    measured = [pair("A", "B", 1.0), pair("B", "C", 1.0), pair("A", "C", 2.012)]
    offsets, _, pairs = solve_timeline(["A", "B", "C"], measured, SETTINGS)
    assert all(p.used for p in pairs)
    assert offsets == pytest.approx({"A": 0.0, "B": 1.004, "C": 2.008}, abs=1e-6)
    assert all(abs(p.residual_ms) <= 4.001 for p in pairs)


def test_unmeasurable_and_unconfident_pairs_are_rejected():
    measured = [
        pair("A", "B", 2.0),
        pair("A", "C", None, None),
        pair("B", "C", 1.0, confidence=1.2),
    ]
    offsets, _, pairs = solve_timeline(["A", "B", "C"], measured, SETTINGS)
    assert offsets == pytest.approx({"A": 0.0, "B": 2.0})
    found = by_clips(pairs)
    assert found[("A", "C")].rejected == "short_overlap"
    assert found[("B", "C")].rejected == "low_confidence"
    assert found[("A", "B")].used


def test_largest_group_wins_and_the_rest_are_separate():
    measured = [pair("A", "B", 1.0, 50.0), pair("C", "D", 2.0), pair("D", "E", 3.0)]
    offsets, _, pairs = solve_timeline(["A", "B", "C", "D", "E"], measured, SETTINGS)
    assert set(offsets) == {"C", "D", "E"}
    assert by_clips(pairs)[("A", "B")].rejected == "separate_group"


def test_equal_groups_are_decided_by_confidence():
    measured = [pair("A", "B", 1.0, 5.0), pair("C", "D", 1.0, 9.0)]
    offsets = solve_timeline(["A", "B", "C", "D"], measured, SETTINGS).offsets
    assert set(offsets) == {"C", "D"}


def test_earliest_clip_starts_at_zero_with_negative_lags():
    offsets = solve_timeline(
        ["A", "B", "C"], [pair("A", "B", -4.0), pair("A", "C", -1.5)], SETTINGS
    ).offsets
    assert offsets == pytest.approx({"A": 4.0, "B": 0.0, "C": 2.5})


def test_single_clip_and_no_clips():
    assert solve_timeline(["A"], [], SETTINGS).offsets == {"A": 0.0}
    assert solve_timeline([], [], SETTINGS) == ({}, {}, [])


def test_nothing_matched_keeps_only_the_first_clip():
    offsets, _, pairs = solve_timeline(["A", "B"], [pair("A", "B", 3.0, confidence=1.0)], SETTINGS)
    assert offsets == {"A": 0.0}
    assert pairs[0].rejected == "low_confidence"


def test_pairs_must_name_listed_clips():
    with pytest.raises(ValueError):
        solve_timeline(["A"], [pair("A", "Z", 1.0)], SETTINGS)


def all_pairs(truth: dict[str, float], wrong: tuple[str, str], wrong_confidence: float):
    """Every pair measured right with confidence 5, except `wrong`: 45 s off (a repeated chorus)."""
    clips = list(truth)
    measured = []
    for i, a in enumerate(clips):
        for b in clips[i + 1 :]:
            lag = truth[b] - truth[a]
            if (a, b) == wrong:
                measured.append(pair(a, b, lag + 45.0, wrong_confidence))
            else:
                measured.append(pair(a, b, lag, 5.0))
    return measured


@pytest.mark.parametrize("wrong_confidence", [5.0, 20.0, 50.0])
def test_a_confident_wrong_pair_cannot_push_out_correct_ones(wrong_confidence):
    truth = {"A": 0.0, "B": 3.0, "C": 6.0, "D": 9.0}
    measured = all_pairs(truth, ("A", "D"), wrong_confidence)
    offsets, _, pairs = solve_timeline(list(truth), measured, SETTINGS)
    assert offsets == pytest.approx(truth, abs=1e-9)
    assert [(p.clip_a, p.clip_b) for p in pairs if p.rejected] == [("A", "D")]
    assert by_clips(pairs)[("A", "D")].rejected == "inconsistent"


def test_a_loop_of_three_disagreeing_pairs_drops_the_least_confident():
    # with three clips nothing says which pair is wrong, so confidence decides
    measured = [pair("A", "B", 5.0, 30.0), pair("B", "C", 7.0, 30.0), pair("A", "C", 50.0, 3.0)]
    offsets, _, pairs = solve_timeline(["A", "B", "C"], measured, SETTINGS)
    assert offsets == pytest.approx({"A": 0.0, "B": 5.0, "C": 12.0})
    assert by_clips(pairs)[("A", "C")].rejected == "inconsistent"


def test_clock_drift_is_solved_for_every_clip():
    offsets = {"A": 0.0, "B": 5.0, "C": 12.0}
    drift = {"A": -50.0, "B": 10.0, "C": 40.0}  # averages 0, like the master clock

    def measured(a: str, b: str) -> PairMeasurement:
        found = pair(a, b, (offsets[b] - offsets[a]) * (1 + drift[a] * 1e-6))  # on A's clock
        found.drift_ppm = drift[b] - drift[a]
        return found

    solution = solve_timeline(
        ["A", "B", "C"], [measured("A", "B"), measured("B", "C"), measured("A", "C")], SETTINGS
    )
    assert solution.offsets == pytest.approx(offsets, abs=1e-9)
    assert solution.drift_ppm == pytest.approx(drift, abs=1e-6)


def test_clips_without_a_drift_measurement_get_none():
    measured = [pair("A", "B", 5.0), pair("B", "C", 7.0)]
    measured[0].drift_ppm = 30.0
    solution = solve_timeline(["A", "B", "C"], measured, SETTINGS)
    assert solution.drift_ppm["C"] is None
    assert [solution.drift_ppm["A"], solution.drift_ppm["B"]] == pytest.approx([-15.0, 15.0])
    # B's lag on its own clock, 7.0 s, is 7.0 / (1 + 15e-6) master seconds
    assert solution.offsets["C"] == pytest.approx(5.0 / (1 - 15e-6) + 7.0 / (1 + 15e-6), abs=1e-9)


# --- sound that repeats: a chorus played twice


def song_with_repeated_chorus(rate: int) -> np.ndarray:
    """Looped music with 40 s sections: verse, chorus, verse, the same chorus again, outro."""
    section = 40 * rate
    song = synth.loop(200, seed=1, rate=rate)
    for index, seed in enumerate((21, 22, 23, None, 24)):
        if seed is not None:  # singing that differs per section
            song[index * section : (index + 1) * section] += synth.scene(40, seed, rate) * 0.5
    song[3 * section : 4 * section] = song[section : 2 * section]
    return song + synth.scene(200, seed=9, rate=rate) * 0.1  # the crowd never repeats


@pytest.mark.xfail(
    strict=True,
    reason="known limit: C heard both choruses, so its true pairs are ambiguous (confidence ~1.6) "
    "and only the false A-B chorus match passes; needs a solver that weighs several candidate "
    "lags per pair",
)
def test_a_repeated_chorus_is_outvoted_by_a_clip_that_heard_both():
    rate = SETTINGS.analysis_rate
    song = song_with_repeated_chorus(rate)
    starts = {"A": 25.0, "B": 108.0, "C": 60.0}  # A: first chorus, B: second chorus, C: between
    phones = {"A": synth.Phone(25.0, 70, snr_db=20, echo=0.3, seed=1),
              "B": synth.Phone(108.0, 70, snr_db=20, echo=0.3, seed=2),
              "C": synth.Phone(60.0, 80, snr_db=20, echo=0.3, seed=3)}  # fmt: skip
    audio = {name: synth.record(song, phone, rate) for name, phone in phones.items()}
    measured = []
    for a, b in [("A", "B"), ("A", "C"), ("B", "C")]:
        found = measure_offset(audio[a], audio[b], rate)
        print(f"{a}-{b}: lag {found.lag_s:.3f} s, confidence {found.confidence:.2f}")
        measured.append(pair(a, b, found.lag_s, found.confidence))
    # A and B never overlap, yet their choruses match: B looks 3 s after A instead of 83 s
    assert by_clips(measured)[("A", "B")].lag_s == pytest.approx(3.0, abs=0.01)

    offsets, _, pairs = solve_timeline(["A", "B", "C"], measured, SETTINGS)
    expected = {name: start - starts["A"] for name, start in starts.items()}
    assert offsets == pytest.approx(expected, abs=0.002)
    assert by_clips(pairs)[("A", "B")].rejected == "inconsistent"


# --- whole event, from videos to timeline.json

TRUE_OFFSETS = {"phone_a.mp4": 0.0, "phone_b.mp4": 8.25, "phone_c.mp4": 17.5}


@pytest.fixture(scope="module")
def synced_event(tmp_path_factory) -> tuple[Path, str]:
    """Three phones film one scene (A and C barely overlap), plus two clips that can't sync."""
    folder = tmp_path_factory.mktemp("sync_sources")
    data_dir = tmp_path_factory.mktemp("sync_data")
    event = synth.scene(40, seed=7)
    recordings = {
        "phone_a.mp4": synth.record(event, synth.Phone(0.0, 20, seed=1)),
        "phone_b.mp4": synth.record(event, synth.Phone(8.25, 22, 0.6, 20, 0.3, seed=2)),
        "phone_c.mp4": synth.record(event, synth.Phone(17.5, 20, 1.3, 25, 0.2, seed=3)),
        "elsewhere.mp4": synth.record(synth.scene(15, seed=99), synth.Phone(0.0, 15, seed=4)),
    }
    for name, samples in recordings.items():
        wav = synth.write_wav(folder / f"{name}.wav", samples)
        seconds = len(samples) / synth.RATE
        run_ffmpeg(f"-v error -f lavfi -i color=c=navy:s=160x120:r=30:d={seconds}", "-i", wav,
                   "-c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest",
                   folder / name)  # fmt: skip
        wav.unlink()
    run_ffmpeg("-v error -f lavfi -i color=c=olive:s=160x120:r=30:d=12",
               "-c:v libx264 -preset ultrafast -pix_fmt yuv420p",
               folder / "silent_cam.mp4")  # fmt: skip
    ingest("garden-party", folder, data_dir=data_dir)
    return data_dir, "garden-party"


@needs_ffmpeg
def test_event_clips_land_on_their_true_offsets(synced_event):
    data_dir, event = synced_event
    timeline = sync_event(event, data_dir=data_dir)
    placed = {c.name: c for c in timeline.clips if c.placed}
    assert set(placed) == set(TRUE_OFFSETS)
    for name, offset in TRUE_OFFSETS.items():
        assert placed[name].offset_s == pytest.approx(offset, abs=0.005), name
        assert placed[name].confidence >= SETTINGS.min_confidence
    assert timeline.duration_s == pytest.approx(37.5, abs=0.1)

    unplaced = {c.name: c.reason for c in timeline.clips if not c.placed}
    assert unplaced == {"elsewhere.mp4": NOT_MATCHED, "silent_cam.mp4": "no usable audio"}
    assert len(timeline.pairs) == 6  # every pair of the four clips with sound, once

    on_disk = load_timeline(data_dir / event)
    assert on_disk == timeline


@needs_ffmpeg
def test_cli_sync(synced_event, capsys):
    data_dir, event = synced_event
    assert main(["sync", event, "--data-dir", str(data_dir)]) == 0
    out = capsys.readouterr().out
    assert "Event garden-party: 3 of 5 clips on one clock" in out
    lines = {line.split()[0]: line for line in out.splitlines() if line.startswith("  ")}
    assert float(lines["phone_b.mp4"].split()[1]) == pytest.approx(8.25, abs=0.005)
    assert lines["elsewhere.mp4"].endswith(f"not placed: {NOT_MATCHED}")
    assert lines["silent_cam.mp4"].endswith("not placed: no usable audio")
    assert "Pairs: 6 measured" in out
    assert f"Timeline: {data_dir / event / TIMELINE_NAME}" in out


def test_cli_sync_errors(tmp_path, capsys):
    assert main(["sync", "nothing-here", "--data-dir", str(tmp_path)]) == 2
    assert "run `scenefold ingest` first" in capsys.readouterr().err
    assert main(["sync", "bad name", "--data-dir", str(tmp_path)]) == 2
    assert "invalid event name" in capsys.readouterr().err


def test_unreadable_manifest_stops_sync(tmp_path):
    event_dir = tmp_path / "broken"
    event_dir.mkdir()
    (event_dir / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SyncError, match="unreadable"):
        sync_event("broken", data_dir=tmp_path)
