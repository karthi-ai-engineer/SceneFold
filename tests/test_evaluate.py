"""Tests for measuring sync error against ground truth (no FFmpeg)."""

import json

import pytest

from scenefold.cli import main
from scenefold.evaluate import (
    EvaluationError,
    GroundTruth,
    Moment,
    evaluate,
    load_ground_truth,
    moments_from_offsets,
)
from scenefold.manifest import now
from scenefold.timeline import ClipPlacement, SyncSettings, Timeline, save_timeline

DURATIONS = {"a.mp4": 60.0, "b.mp4": 50.0, "c.mp4": 40.0}
OFFSETS = {"a.mp4": 0.0, "b.mp4": 12.5, "c.mp4": 30.0}
DRIFT = {"a.mp4": -20.0, "b.mp4": 5.0, "c.mp4": 15.0}


def timeline(
    offsets: dict[str, float | None], drift: dict[str, float] | None = None, names=None
) -> Timeline:
    drift = drift or {}
    clips = []
    for index, (key, offset) in enumerate(offsets.items()):
        name = (names or {}).get(key, key)
        clips.append(
            ClipPlacement(
                clip_id=f"clip{index}",
                name=name,
                placed=offset is not None,
                offset_s=offset,
                drift_ppm=drift.get(key),
                duration_s=DURATIONS.get(key, 60.0),
                reason=None if offset is not None else "no confident audio match",
            )
        )
    return Timeline(
        event_id="test-event",
        created_at=now(),
        settings=SyncSettings(),
        duration_s=70.0,
        clips=clips,
        pairs=[],
    )


def test_a_perfect_timeline_has_no_error():
    truth = moments_from_offsets(OFFSETS, DURATIONS, DRIFT)
    result = evaluate(timeline(OFFSETS, DRIFT), truth)
    assert result.errors
    assert result.worst.error_ms == pytest.approx(0.0, abs=1e-3)
    assert result.within_frame == len(result.errors)
    assert result.not_placed == []


def test_a_misplaced_clip_shows_in_its_pairs():
    truth = moments_from_offsets(OFFSETS, DURATIONS)
    result = evaluate(timeline({**OFFSETS, "c.mp4": 30.05}), truth)
    by_pair = {(e.clip_a, e.clip_b): e.error_ms for e in result.errors}
    assert by_pair[("a.mp4", "b.mp4")] == pytest.approx(0.0, abs=1e-3)
    assert by_pair[("a.mp4", "c.mp4")] == pytest.approx(50.0, abs=1e-3)
    assert by_pair[("b.mp4", "c.mp4")] == pytest.approx(50.0, abs=1e-3)
    assert abs(result.worst.error_ms) == pytest.approx(50.0, abs=1e-3)
    assert result.within_frame == sum(
        1 for e in result.errors if "c.mp4" not in (e.clip_a, e.clip_b)
    )


def test_where_master_time_starts_does_not_matter():
    truth = moments_from_offsets(OFFSETS, DURATIONS)
    shifted = {name: offset + 100.0 for name, offset in OFFSETS.items()}
    assert evaluate(timeline(shifted), truth).worst.error_ms == pytest.approx(0.0, abs=1e-3)


def test_ignoring_drift_shows_up_as_growing_error():
    truth = moments_from_offsets(OFFSETS, DURATIONS, DRIFT)
    result = evaluate(timeline(OFFSETS), truth)  # the timeline forgot the drift
    pairs = [e for e in result.errors if (e.clip_a, e.clip_b) == ("a.mp4", "b.mp4")]
    assert abs(pairs[-1].error_ms) > abs(pairs[0].error_ms)
    # at master time 60 s: b drifted 5 ppm over 47.5 s and a -20 ppm over 60 s
    assert pairs[-1].error_ms == pytest.approx((47.5 * 5e-6 + 60 * 20e-6) * 1000, abs=1e-3)


def test_unplaced_clips_are_listed_not_scored():
    truth = moments_from_offsets(OFFSETS, DURATIONS)
    result = evaluate(timeline({**OFFSETS, "b.mp4": None}), truth)
    assert result.not_placed == ["b.mp4"]
    assert all("b.mp4" not in (e.clip_a, e.clip_b) for e in result.errors)


def test_clips_can_be_named_by_clip_id():
    truth = GroundTruth(moments=[Moment(times={"clip0": 1.0, "b.mp4": 0.5})])
    result = evaluate(timeline({"a.mp4": 0.0, "b.mp4": 0.5}), truth)
    assert result.errors[0].error_ms == pytest.approx(0.0)
    assert result.errors[0].moment == "moment 1"


def test_same_file_names_must_use_clip_ids():
    names = {"a.mp4": "VID_0001.mp4", "b.mp4": "VID_0001.mp4"}
    same_names = timeline({"a.mp4": 0.0, "b.mp4": 1.0}, names=names)
    truth = GroundTruth(moments=[Moment(times={"VID_0001.mp4": 1.0, "clip1": 0.0})])
    with pytest.raises(EvaluationError, match="clip0, clip1"):
        evaluate(same_names, truth)


def test_unknown_clip_names_are_reported():
    truth = GroundTruth(moments=[Moment(times={"a.mp4": 1.0, "missing.mp4": 1.0})])
    with pytest.raises(EvaluationError, match="missing.mp4"):
        evaluate(timeline(OFFSETS), truth)


def test_moments_only_where_two_clips_overlap():
    truth = moments_from_offsets({"a": 0.0, "b": 25.0}, {"a": 30.0, "b": 30.0}, every_s=5.0)
    assert [sorted(m.times) for m in truth.moments] == [["a", "b"], ["a", "b"]]  # at 25 s and 30 s
    with pytest.raises(EvaluationError, match="overlap"):
        moments_from_offsets({"a": 0.0, "b": 50.0}, {"a": 30.0, "b": 30.0})


def test_bad_ground_truth_files(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    empty = tmp_path / "empty.json"
    empty.write_text('{"moments": []}', encoding="utf-8")
    for path in (broken, empty, tmp_path / "missing.json"):
        with pytest.raises(EvaluationError):
            load_ground_truth(path)


def test_cli_evaluate(tmp_path, capsys):
    event_dir = tmp_path / "test-event"
    event_dir.mkdir()
    save_timeline(event_dir, timeline({**OFFSETS, "c.mp4": 30.1, "d.mp4": None}))
    truth = moments_from_offsets(OFFSETS, DURATIONS)
    truth.moments[0].times["d.mp4"] = 1.0
    truth_path = tmp_path / "truth.json"
    truth_path.write_text(truth.model_dump_json(), encoding="utf-8")

    assert main(["evaluate", "test-event", str(truth_path), "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    count = len(json.loads(truth_path.read_text())["moments"])
    assert "Event test-event: " in out and f"at {count} moments" in out
    assert "not placed: d.mp4" in out
    assert "worst +100.0 ms" in out  # c.mp4 sits 100 ms late, so it puts moments later
    assert "within one frame (33 ms):" in out


def test_cli_evaluate_errors(tmp_path, capsys):
    truth_path = tmp_path / "truth.json"
    truth_path.write_text('{"moments": [{"times": {"a.mp4": 1.0}}]}', encoding="utf-8")
    assert main(["evaluate", "nothing-here", str(truth_path), "--data-dir", str(tmp_path)]) == 2
    assert "run `scenefold sync` first" in capsys.readouterr().err

    event_dir = tmp_path / "test-event"
    event_dir.mkdir()
    save_timeline(event_dir, timeline(OFFSETS))
    assert main(["evaluate", "test-event", str(truth_path), "--data-dir", str(tmp_path)]) == 1
    assert "nothing could be compared" in capsys.readouterr().out
