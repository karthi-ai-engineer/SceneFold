"""Merging the clips' accounts: does the same thing seen by three phones become one event?"""

from datetime import UTC, datetime

import pytest

from scenefold.fuse import (
    FuseError,
    OnTheClock,
    describing,
    fuse_event,
    gather,
    local_time,
    master_time,
    on_the_clock,
    recording_at,
)
from scenefold.judge import Reading
from scenefold.knowledge import (
    NOT_IN_VIEW,
    READ_DIFFERENTLY,
    all_events,
    counts,
    events_between,
    events_with_conflicts,
    load_store,
    open_store,
)
from scenefold.manifest import (
    Clip,
    ClipStatus,
    Manifest,
    Proxy,
    ProxySettings,
    Source,
    save_manifest,
)
from scenefold.observations import (
    ClipObservations,
    Moment,
    Observation,
    WatchSettings,
    save_observations,
)
from scenefold.timeline import ClipPlacement, SyncSettings, Timeline, save_timeline

WHEN = datetime(2026, 9, 21, tzinfo=UTC)


def placed(clip_id, offset_s, duration_s=60.0, late_s=None, drift_ppm=None):
    return ClipPlacement(
        clip_id=clip_id,
        name=f"{clip_id}.mp4",
        placed=True,
        offset_s=offset_s,
        duration_s=duration_s,
        heard_late_s=late_s,
        drift_ppm=drift_ppm,
        confidence=5.0,
    )


def watched(clip_id, moments, observations=(), duration_s=60.0):
    return ClipObservations(
        clip_id=clip_id,
        name=f"{clip_id}.mp4",
        created_at=WHEN,
        settings=WatchSettings(),
        duration_s=duration_s,
        seconds_taken=1.0,
        observations=list(observations),
        moments=[Moment(t_s=t, kind=k, strength=s) for t, k, s in moments],
    )


def shows(t_start_s, t_end_s, summary, score=0.5):
    return Observation(
        t_start_s=t_start_s,
        t_end_s=t_end_s,
        summary=summary,
        subjects=[],
        picture_score=score,
    )


def build(tmp_path, clips: dict, placements: list[ClipPlacement]) -> str:
    """An event workspace holding only what fusing needs: a timeline and the watched clips."""
    event_dir = tmp_path / "an-event"
    manifest = Manifest(
        event_id="an-event",
        created_at=WHEN,
        updated_at=WHEN,
        proxy_settings=ProxySettings(),
        clips=[
            Clip(
                clip_id=p.clip_id,
                status=ClipStatus.OK,
                ingested_at=WHEN,
                source=Source(
                    name=p.name, path=f"/in/{p.name}", size_bytes=1, modified_ns=1, sha256=""
                ),
                proxy=Proxy(
                    video=f"proxies/{p.clip_id}.mp4",
                    audio=f"proxies/{p.clip_id}.wav",
                    width=1280,
                    height=720,
                    fps=30,
                    duration_s=p.duration_s,
                    settings_key="k",
                ),
            )
            for p in placements
        ],
    )
    event_dir.mkdir(parents=True, exist_ok=True)
    save_manifest(event_dir, manifest)
    save_timeline(
        event_dir,
        Timeline(
            event_id="an-event",
            created_at=WHEN,
            settings=SyncSettings(),
            duration_s=max(p.offset_s + p.duration_s for p in placements),
            clips=placements,
            pairs=[],
        ),
    )
    for seen in clips.values():
        save_observations(event_dir, seen)
    return "an-event"


def test_a_clips_own_seconds_become_shared_ones():
    """A clip that started late, ran fast, and stood far from the stage still lands right."""
    clip = placed("a", offset_s=12.5, late_s=0.2, drift_ppm=100)

    assert master_time(clip, 0) == pytest.approx(12.7)
    assert master_time(clip, 10) == pytest.approx(22.6989, abs=0.001)  # its clock runs fast
    assert local_time(clip, master_time(clip, 7.5)) == pytest.approx(7.5)
    assert recording_at(clip, 12.7) and recording_at(clip, 60)
    assert not recording_at(clip, 12.6) and not recording_at(clip, 80)


def test_faint_moments_are_left_out():
    clip = placed("a", 0)
    moments = [
        Moment(t_s=1.0, kind="sound", strength=0.9),
        Moment(t_s=2.0, kind="sound", strength=0.1),
    ]

    kept = on_the_clock(clip, moments)

    assert [m.t_local_s for m in kept] == [1.0]


def test_the_same_flash_in_three_clips_is_one_event():
    together = [
        OnTheClock("a", 10.00, 10.0, "picture", 1.0),
        OnTheClock("b", 10.05, 3.0, "picture", 0.8),
        OnTheClock("c", 10.12, 7.5, "sound", 0.6),
        OnTheClock("a", 30.00, 30.0, "sound", 0.7),
    ]

    groups = gather(together)

    assert len(groups) == 2
    assert {m.clip_id for m in groups[0]} == {"a", "b", "c"}
    assert [m.clip_id for m in groups[1]] == ["a"]


def test_one_clip_shaking_twice_is_not_two_witnesses():
    twice = [
        OnTheClock("a", 10.00, 10.0, "picture", 0.4),
        OnTheClock("a", 10.06, 10.06, "picture", 0.9),
        OnTheClock("b", 10.03, 5.0, "picture", 0.5),
    ]

    group = gather(twice)[0]

    assert len(group) == 2
    assert next(m for m in group if m.clip_id == "a").strength == 0.9  # the stronger one stands


def test_what_a_clip_was_showing_around_a_moment():
    seen = watched("a", [], [shows(0, 10, "the lights go down"), shows(10, 20, "confetti")])

    assert describing(seen, 4).summary == "the lights go down"
    assert describing(seen, 15).summary == "confetti"
    assert describing(seen, 24).summary == "confetti"  # nothing covers it, so the nearest window
    assert describing(seen, 400) is None  # too far from anything said


def test_three_phones_catching_one_flash_become_one_event_with_three_witnesses(tmp_path):
    placements = [placed("a", 0.0), placed("b", 5.0), placed("c", 10.0)]
    clips = {
        "a": watched("a", [(20.0, "picture", 1.0)], [shows(12, 24, "a bright flash", 0.8)]),
        "b": watched("b", [(15.02, "picture", 0.9)], [shows(12, 24, "a flash of light", 0.5)]),
        "c": watched("c", [(10.05, "sound", 0.7)], [shows(6, 18, "a bang", 0.4)]),
    }
    name = build(tmp_path, clips, placements)

    events = fuse_event(name, data_dir=tmp_path)

    assert len(events) == 1
    event = events[0]
    assert event.t_master_s == pytest.approx(20.02, abs=0.05)
    assert event.witnesses == 3
    assert event.kind == "both"
    assert event.summary == "a bright flash"  # the clip whose picture was best
    assert {e.clip_id for e in event.evidence} == {"a", "b", "c"}
    assert not event.conflicts  # everyone filming caught it


def test_a_moment_two_caught_and_one_missed_is_a_conflict(tmp_path):
    """Two clips agree something happened; a third with a decent view says nothing."""
    placements = [placed("a", 0.0), placed("b", 0.0), placed("c", 0.0)]
    clips = {
        "a": watched("a", [(20.0, "picture", 1.0)], [shows(12, 24, "confetti falls", 0.95)]),
        "b": watched("b", [(20.04, "picture", 0.8)], [shows(12, 24, "confetti", 0.82)]),
        "c": watched("c", [], [shows(12, 24, "the crowd, from behind", 0.83)]),
    }
    name = build(tmp_path, clips, placements)

    events = fuse_event(name, data_dir=tmp_path)

    conflict = events[0].conflicts[0]
    assert conflict.kind == NOT_IN_VIEW
    assert "1 with as good a view did not" in conflict.explanation
    assert conflict.resolved_by == "a"  # of the three, its picture was clearly the best
    assert "better one" in conflict.reason


def test_cameras_as_good_as_each_other_leave_it_unresolved(tmp_path):
    """Nobody had the better view, so nobody's account wins: that is the honest answer."""
    placements = [placed("a", 0.0), placed("b", 0.0), placed("c", 0.0)]
    clips = {
        "a": watched("a", [(20.0, "picture", 1.0)], [shows(12, 24, "something falls", 0.50)]),
        "b": watched("b", [(20.05, "picture", 0.9)], [shows(12, 24, "something", 0.50)]),
        "c": watched("c", [], [shows(12, 24, "nothing much", 0.51)]),
    }
    name = build(tmp_path, clips, placements)

    events = fuse_event(name, data_dir=tmp_path)

    conflict = events[0].conflicts[0]
    assert conflict.resolved_by is None
    assert "no camera had a clearly better view" in conflict.reason


def test_everything_is_written_down_and_can_be_asked_about(tmp_path):
    placements = [placed("a", 0.0), placed("b", 0.0)]
    clips = {
        "a": watched(
            "a", [(5.0, "sound", 0.9), (40.0, "picture", 0.8)], [shows(0, 12, "a bang", 0.7)]
        ),
        "b": watched("b", [(5.03, "sound", 0.6)], [shows(0, 12, "a bang too", 0.6)]),
    }
    name = build(tmp_path, clips, placements)
    fuse_event(name, data_dir=tmp_path)

    db = load_store(tmp_path / name)
    try:
        known = counts(db)
        assert known["clips"] == 2
        assert known["events"] == 2
        assert known["corroborated"] == 1  # only the bang was caught by both
        assert known["conflicts"] == 0  # nobody with a good view missed anything
        assert len(events_between(db, 0, 10)) == 1
        assert len(all_events(db)) == 2
        assert events_with_conflicts(db) == []  # nothing here needs a person to look
        first = events_between(db, 0, 10)[0]
        assert {e.clip_id for e in first.evidence} == {"a", "b"}
        assert first.summary == "a bang"  # the better-looking of the two accounts
    finally:
        db.close()


def test_fusing_again_replaces_what_was_there(tmp_path):
    placements = [placed("a", 0.0)]
    clips = {"a": watched("a", [(5.0, "sound", 0.9)], [shows(0, 12, "a bang", 0.7)])}
    name = build(tmp_path, clips, placements)
    fuse_event(name, data_dir=tmp_path)

    clips["a"] = watched("a", [(9.0, "sound", 0.9)], [shows(0, 12, "a different bang", 0.7)])
    save_observations(tmp_path / name, clips["a"])
    fuse_event(name, data_dir=tmp_path)

    db = load_store(tmp_path / name)
    try:
        assert counts(db)["events"] == 1  # not two: the old picture was replaced, not added to
        assert all_events(db)[0].t_master_s == pytest.approx(9.0)
    finally:
        db.close()


def test_nothing_watched_yet_says_what_to_do(tmp_path):
    name = build(tmp_path, {}, [placed("a", 0.0)])

    with pytest.raises(FuseError, match="observe"):
        fuse_event(name, data_dir=tmp_path)


def test_no_timeline_says_what_to_do(tmp_path):
    (tmp_path / "bare").mkdir()

    with pytest.raises(FuseError, match="sync"):
        fuse_event("bare", data_dir=tmp_path)


def test_a_fresh_store_is_empty_but_usable(tmp_path):
    db = open_store(tmp_path / "new")
    try:
        assert counts(db) == {
            "clips": 0,
            "events": 0,
            "corroborated": 0,
            "evidence": 0,
            "conflicts": 0,
            "unresolved": 0,
        }
        assert all_events(db) == []
    finally:
        db.close()


def test_a_camera_pointing_elsewhere_is_not_disagreeing(tmp_path):
    """Two clips catch a flash; a third was filming the floor. That is not a disagreement."""
    placements = [placed("a", 0.0), placed("b", 0.0), placed("floor", 0.0)]
    clips = {
        "a": watched("a", [(20.0, "picture", 1.0)], [shows(12, 24, "a flash", 0.8)]),
        "b": watched("b", [(20.02, "picture", 0.9)], [shows(12, 24, "a flash too", 0.7)]),
        "floor": watched("floor", [], [shows(12, 24, "somebody's shoes", 0.2)]),
    }
    name = build(tmp_path, clips, placements)

    events = fuse_event(name, data_dir=tmp_path)

    assert events[0].witnesses == 2
    assert not events[0].conflicts  # the third had no view worth calling a miss


def test_one_clip_alone_is_not_a_disagreement(tmp_path):
    """A single phone twitching is that phone, not the others failing to see something."""
    placements = [placed("a", 0.0), placed("b", 0.0)]
    clips = {
        "a": watched("a", [(20.0, "picture", 0.9)], [shows(12, 24, "a jolt", 0.8)]),
        "b": watched("b", [], [shows(12, 24, "the stage, steady", 0.8)]),
    }
    name = build(tmp_path, clips, placements)

    events = fuse_event(name, data_dir=tmp_path)

    assert events[0].witnesses == 1
    assert not events[0].conflicts


def test_a_clip_with_as_good_a_view_missing_it_is_a_disagreement(tmp_path):
    placements = [placed("a", 0.0), placed("b", 0.0), placed("c", 0.0)]
    clips = {
        "a": watched("a", [(20.0, "picture", 1.0)], [shows(12, 24, "confetti", 0.80)]),
        "b": watched("b", [(20.03, "picture", 0.9)], [shows(12, 24, "confetti as well", 0.78)]),
        "c": watched("c", [], [shows(12, 24, "the same stage, nothing falling", 0.98)]),
    }
    name = build(tmp_path, clips, placements)

    events = fuse_event(name, data_dir=tmp_path)

    conflict = events[0].conflicts[0]
    assert "1 with as good a view did not" in conflict.explanation
    assert conflict.resolved_by is None  # the clip that missed it had the best view of all
    assert "best view" in conflict.reason


class FakeReader:
    """A reader that says what it is told to, and counts how often it was asked."""

    def __init__(self, verdicts=None):
        self.asked: list[tuple[str, str]] = []
        self.verdicts = verdicts or {}

    def read(self, first, second):
        self.asked.append((first, second))
        return self.verdicts.get((first, second), Reading(True, None, "they fit"))


def test_accounts_that_do_not_fit_become_a_conflict(tmp_path):
    placements = [placed("a", 0.0), placed("b", 0.0)]
    clips = {
        "a": watched("a", [(20.0, "picture", 1.0)], [shows(12, 24, "confetti falls", 0.90)]),
        "b": watched("b", [(20.02, "picture", 0.9)], [shows(12, 24, "an empty dark stage", 0.70)]),
    }
    name = build(tmp_path, clips, placements)
    cannot_both = Reading(False, READ_DIFFERENTLY, "both cannot hold")
    reader = FakeReader({("confetti falls", "an empty dark stage"): cannot_both})

    events = fuse_event(name, data_dir=tmp_path, judge=reader)

    conflict = next(c for c in events[0].conflicts if c.kind == READ_DIFFERENTLY)
    assert "both cannot hold" in conflict.explanation
    assert conflict.resolved_by == "a"  # its picture was the better one, 0.90 against 0.70
    assert reader.asked == [("confetti falls", "an empty dark stage")]


def test_accounts_that_fit_are_left_alone(tmp_path):
    placements = [placed("a", 0.0), placed("b", 0.0)]
    clips = {
        "a": watched("a", [(20.0, "picture", 1.0)], [shows(12, 24, "a crowd with lights", 0.9)]),
        "b": watched("b", [(20.02, "picture", 0.9)], [shows(12, 24, "people holding phones", 0.8)]),
    }
    name = build(tmp_path, clips, placements)

    events = fuse_event(name, data_dir=tmp_path, judge=FakeReader())

    assert not events[0].conflicts


def test_the_same_two_sentences_are_only_read_once(tmp_path):
    """A twelve-second description covers many moments; reading it again each time is waste."""
    placements = [placed("a", 0.0), placed("b", 0.0)]
    clips = {
        "a": watched(
            "a",
            [(20.0, "picture", 1.0), (21.0, "sound", 0.9), (22.0, "sound", 0.8)],
            [shows(12, 24, "one account", 0.9)],
        ),  # fmt: skip
        "b": watched(
            "b",
            [(20.02, "picture", 0.9), (21.03, "sound", 0.8), (22.01, "sound", 0.7)],
            [shows(12, 24, "another account", 0.8)],
        ),  # fmt: skip
    }
    name = build(tmp_path, clips, placements)
    reader = FakeReader()

    events = fuse_event(name, data_dir=tmp_path, judge=reader)

    assert len(events) == 3  # three separate moments
    assert len(reader.asked) == 1  # but one pair of sentences


def test_identical_accounts_are_not_read_at_all(tmp_path):
    placements = [placed("a", 0.0), placed("b", 0.0)]
    same = "a crowd with lights"
    clips = {
        "a": watched("a", [(20.0, "picture", 1.0)], [shows(12, 24, same, 0.9)]),
        "b": watched("b", [(20.02, "picture", 0.9)], [shows(12, 24, same, 0.8)]),
    }
    name = build(tmp_path, clips, placements)
    reader = FakeReader()

    fuse_event(name, data_dir=tmp_path, judge=reader)

    assert reader.asked == []


def test_nothing_is_read_when_only_one_clip_saw_it(tmp_path):
    placements = [placed("a", 0.0), placed("b", 0.0)]
    clips = {
        "a": watched("a", [(20.0, "picture", 1.0)], [shows(12, 24, "a flash", 0.9)]),
        "b": watched("b", [], [shows(12, 24, "the floor", 0.2)]),
    }
    name = build(tmp_path, clips, placements)
    reader = FakeReader()

    fuse_event(name, data_dir=tmp_path, judge=reader)

    assert reader.asked == []  # there is only one account of it
