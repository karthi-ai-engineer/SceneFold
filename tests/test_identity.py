"""Matching people across angles: the maths, on cases whose answer is known by hand.

Real footage cannot tell us whether a match was right — nobody has labelled who is who in it.
These are hand-written descriptions of the kind the model actually produces, with the true answer
written down beside them, so the threshold is measured against something rather than chosen.
"""

import json
from datetime import UTC, datetime

import pytest

from scenefold.identity import (
    SAME_PERSON,
    SURE,
    IdentityError,
    Tracklet,
    colours,
    describes_somebody,
    describing,
    find_people,
    identify_event,
    link_clip,
    look_alike,
    match_clips,
    pairs,
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
    PeopleSettings,
    Sighting,
    WatchSettings,
    save_observations,
)
from scenefold.timeline import ClipPlacement, SyncSettings, Timeline, save_timeline

# Pairs of descriptions of the SAME person, as two angles would word them. Taken from the shapes
# the model gave on real stage footage: same clothes, different wording, sometimes less of them.
SAME = [
    ("a red sleeveless top", "red sleeveless top and dark jeans"),
    ("black sleeveless top and dark pants", "a woman in a black sleeveless top"),
    ("white long-sleeve shirt", "a man in a white shirt"),
    ("purple sweater and a hat", "purple sweater"),
    ("maroon long-sleeve shirt, black trousers", "maroon shirt"),
    ("yellow top, blue jeans", "a yellow top and blue jeans"),
]
# Pairs of descriptions of DIFFERENT people at the same event.
DIFFERENT = [
    ("a red sleeveless top", "a white long-sleeve shirt"),
    ("black sleeveless top and dark pants", "yellow top, blue jeans"),
    ("purple sweater and a hat", "maroon long-sleeve shirt"),
    ("green jacket over a white shirt", "red dress"),
    ("blue jeans and a grey hoodie", "black dress"),
    ("a man in a white shirt", "a woman in a red skirt"),
]


def test_a_description_needs_a_colour_and_a_garment_to_pick_out_a_person():
    assert describes_somebody("a red sleeveless top")
    assert describes_somebody("white shirt and black trousers")
    assert not describes_somebody("a person")  # picks out everybody, so nobody
    assert not describes_somebody("a man in a shirt")
    assert not describes_somebody("dark jeans")  # half the event, after sunset
    assert not describes_somebody("standing near the front")


def test_clothes_are_read_as_colour_and_garment():
    assert pairs("a red sleeveless top and blue jeans") == {
        ("red", "top"),
        ("sleeveless", "top"),
        ("blue", "jeans"),
    }
    assert ("dark-blue", "jacket") in pairs("a dark blue jacket")
    assert colours("maroon shirt, black trousers") == {"maroon", "black"}


@pytest.mark.parametrize(("one", "other"), SAME)
def test_the_same_person_worded_twice_scores_above_the_bar(one, other):
    assert look_alike(one, other) >= SAME_PERSON, f"{one!r} vs {other!r}"


@pytest.mark.parametrize(("one", "other"), DIFFERENT)
def test_different_people_score_below_the_bar(one, other):
    assert look_alike(one, other) < SAME_PERSON, f"{one!r} vs {other!r}"


def test_the_bar_sits_in_the_gap_between_the_two(capsys):
    """Not just either side of the line — there is room between them, which is what matters."""
    same = [look_alike(a, b) for a, b in SAME]
    different = [look_alike(a, b) for a, b in DIFFERENT]
    print(
        f"same: {min(same):.2f}-{max(same):.2f}  "
        f"different: {min(different):.2f}-{max(different):.2f}"
    )
    assert min(same) > max(different)
    assert min(same) - max(different) > 0.2  # a real gap, not a coincidence of two numbers


def test_nothing_in_common_is_not_a_match():
    assert look_alike("a red top", "a red top") == 1.0
    assert look_alike("a red top", "the stage is empty") == 0.0
    assert look_alike("", "a red top") == 0.0


def _seen(t, wearing, doing=""):
    return Sighting(t_start_s=t, t_end_s=t + 10, wearing=wearing, doing=doing)


def test_one_person_across_windows_is_one_person():
    found = link_clip(
        "clip-a",
        [
            _seen(0, "a red sleeveless top"),
            _seen(10, "red sleeveless top and jeans"),
            _seen(20, "a woman in a red top"),
        ],
    )
    assert len(found) == 1
    assert found[0].sightings == 3
    assert found[0].t_start_s == 0 and found[0].t_end_s == 30


def test_two_people_in_one_window_stay_two_people_however_alike():
    # Same window, same words: however similar, they cannot be one person - both were on screen.
    found = link_clip(
        "clip-a", [_seen(0, "a black t-shirt and jeans"), _seen(0, "a black t-shirt and jeans")]
    )
    assert len(found) == 2


def test_someone_who_left_the_frame_and_came_back_is_taken_up_again():
    # Clothes do not change during an event, and a person split in two cannot be matched to
    # another angle at all, because each angle's people are matched one to one.
    found = link_clip(
        "clip-a", [_seen(0, "a red sleeveless top"), _seen(500, "a red sleeveless top")]
    )
    assert len(found) == 1
    assert found[0].t_start_s == 0 and found[0].t_end_s == 510


def test_bridging_a_long_gap_takes_more_than_a_passing_resemblance():
    # A red top and a red shirt are not the same garment. Frame to frame that is forgivable;
    # across four minutes it is a different person.
    found = link_clip("clip-a", [_seen(0, "a red top"), _seen(500, "a red shirt")])
    assert len(found) == 2


def test_a_description_that_picks_out_nobody_is_dropped():
    assert link_clip("clip-a", [_seen(0, "a person"), _seen(10, "dark clothing")]) == []


def _track(name, clip, wearing, start=0.0, end=30.0):
    return Tracklet(
        tracklet_id=name,
        clip_id=clip,
        t_start_s=start,
        t_end_s=end,
        t_master_start_s=start,
        t_master_end_s=end,
        wearing=wearing,
        doing="",
        sightings=3,
    )


def test_two_angles_of_the_same_people_are_matched_up():
    left = [
        _track("l1", "clip-a", "a red sleeveless top"),
        _track("l2", "clip-a", "white long-sleeve shirt"),
    ]
    right = [
        _track("r1", "clip-b", "white shirt and dark trousers"),
        _track("r2", "clip-b", "red sleeveless top and jeans"),
    ]
    matched = {(one.tracklet_id, other.tracklet_id) for one, other, _ in match_clips(left, right)}
    assert matched == {("l1", "r2"), ("l2", "r1")}


def test_the_whole_set_is_weighed_at_once_not_one_greedy_pair_at_a_time():
    # Greedy would give l1 its best partner (r1, the fuller description of the same red top) and
    # leave l2 unmatched; the right answer pairs both.
    left = [_track("l1", "clip-a", "a red top"), _track("l2", "clip-a", "a red top and jeans")]
    right = [
        _track("r1", "clip-b", "a red top and jeans"),
        _track("r2", "clip-b", "a red top"),
    ]
    matched = {(one.tracklet_id, other.tracklet_id) for one, other, _ in match_clips(left, right)}
    assert len(matched) == 2


def test_nobody_is_forced_to_match():
    left = [_track("l1", "clip-a", "a red sleeveless top")]
    right = [_track("r1", "clip-b", "a green jacket")]
    assert match_clips(left, right) == []


def test_being_on_screen_together_helps_a_match_but_cannot_make_one():
    together = match_clips(
        [_track("l1", "clip-a", "a red top and blue jeans", 0, 30)],
        [_track("r1", "clip-b", "a green jacket and blue jeans", 0, 30)],
    )
    apart = match_clips(
        [_track("l1", "clip-a", "a red top and blue jeans", 0, 30)],
        [_track("r1", "clip-b", "a green jacket and blue jeans", 100, 130)],
    )
    assert apart and together
    assert together[0][2] > apart[0][2]
    # but it never rescues two people who look nothing alike
    assert not match_clips(
        [_track("l1", "clip-a", "a red top", 0, 30)],
        [_track("r1", "clip-b", "a green jacket", 0, 30)],
    )


def test_somebody_only_one_phone_filmed_is_still_somebody():
    people = find_people([
        _track("l1", "clip-a", "a red sleeveless top"),
        _track("r1", "clip-b", "red sleeveless top and jeans"),
        _track("l2", "clip-a", "a green jacket"),  # nobody else caught them
    ])  # fmt: skip
    across = [p for p in people if len(p.clips) > 1]
    alone = [p for p in people if len(p.clips) == 1]
    assert len(across) == 1 and len(alone) == 1
    assert alone[0].wearing == "a green jacket"
    assert across[0].clips == ["clip-a", "clip-b"]


def test_three_angles_of_one_person_become_one_person():
    people = find_people([
        _track("a1", "clip-a", "a red sleeveless top"),
        _track("b1", "clip-b", "red sleeveless top and jeans"),
        _track("c1", "clip-c", "a woman in a red top"),
    ])  # fmt: skip
    assert len(people) == 1
    assert people[0].clips == ["clip-a", "clip-b", "clip-c"]


def test_a_match_the_words_barely_support_is_reported_as_unsure():
    # Enough to join, not enough to be sure of: the jeans agree, the tops do not.
    one, other = "a red top and blue jeans", "a green jacket and blue jeans"
    score = look_alike(one, other)
    if score < SAME_PERSON:
        pytest.skip("this pair does not join at all, which is also an honest answer")
    people = find_people([_track("a1", "clip-a", one), _track("b1", "clip-b", other)])
    assert len(people[0].clips) == 2
    assert people[0].sure == (score >= SURE)


def test_tracklets_are_put_on_the_shared_clock():
    placed = ClipPlacement(
        clip_id="clip-a",
        name="left.mp4",
        placed=True,
        offset_s=12.0,
        duration_s=60.0,
        drift_ppm=0.0,
        heard_late_s=0.0,
    )
    found = link_clip("clip-a", [_seen(0, "a red top"), _seen(10, "a red top")], placed)
    assert found[0].t_start_s == 0.0
    assert found[0].t_master_start_s == 12.0
    assert found[0].t_master_end_s == 32.0


def _event(tmp_path, people_by_clip: dict[str, list[Sighting]], offsets: dict[str, float]):
    """An event workspace holding only what identifying needs: a timeline and asked clips."""
    when = datetime(2026, 9, 21, tzinfo=UTC)
    placements = [
        ClipPlacement(
            clip_id=clip_id,
            name=f"{clip_id}.mp4",
            placed=True,
            offset_s=offset,
            duration_s=60.0,
            confidence=5.0,
        )
        for clip_id, offset in offsets.items()
    ]
    event_dir = tmp_path / "an-event"
    event_dir.mkdir(parents=True, exist_ok=True)
    save_manifest(
        event_dir,
        Manifest(
            event_id="an-event",
            created_at=when,
            updated_at=when,
            proxy_settings=ProxySettings(),
            clips=[
                Clip(
                    clip_id=p.clip_id,
                    status=ClipStatus.OK,
                    ingested_at=when,
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
        ),
    )
    save_timeline(
        event_dir,
        Timeline(
            event_id="an-event",
            created_at=when,
            settings=SyncSettings(),
            duration_s=120.0,
            clips=placements,
            pairs=[],
        ),
    )
    for clip_id, sightings in people_by_clip.items():
        save_observations(
            event_dir,
            ClipObservations(
                clip_id=clip_id,
                name=f"{clip_id}.mp4",
                created_at=when,
                settings=WatchSettings(),
                duration_s=60.0,
                seconds_taken=1.0,
                observations=[],
                moments=[],
                people=sightings,
                people_settings=PeopleSettings(),
                people_seconds_taken=1.0,
            ),
        )
    return "an-event"


def test_an_event_from_end_to_end(tmp_path):
    event = _event(
        tmp_path,
        {
            "left": [_seen(0, "a red sleeveless top"), _seen(10, "red sleeveless top and jeans")],
            "right": [_seen(0, "a woman in a red top"), _seen(10, "a green jacket")],
        },
        {"left": 0.0, "right": 5.0},
    )
    people = identify_event(event, data_dir=tmp_path)
    across = [p for p in people if len(p.clips) > 1]
    assert len(across) == 1
    assert across[0].clips == ["left", "right"]

    written = json.loads((tmp_path / "an-event" / "people.json").read_text(encoding="utf-8"))
    assert written["found"]["across_angles"] == 1
    assert written["clips_asked"] == 2
    matched = next(p for p in written["people"] if p["across_angles"])
    assert matched["joins"] and matched["joins"][0]["score"] >= SAME_PERSON
    # the right-hand clip started five seconds later, so its sightings sit five seconds along
    right = next(s for s in matched["seen"] if s["clip_id"] == "right")
    assert right["t_master_start_s"] == right["t_start_s"] + 5.0


def test_an_event_nobody_has_been_asked_about_is_refused(tmp_path):
    event = _event(tmp_path, {}, {"left": 0.0})
    with pytest.raises(IdentityError, match="has been asked who was in it"):
        identify_event(event, data_dir=tmp_path)


def test_an_event_with_no_timeline_is_refused(tmp_path):
    event = _event(tmp_path, {"left": [_seen(0, "a red top")]}, {"left": 0.0})
    (tmp_path / "an-event" / "timeline.json").unlink()
    with pytest.raises(IdentityError, match="no timeline yet"):
        identify_event(event, data_dir=tmp_path)


def test_two_people_on_screen_together_are_never_chained_into_one():
    # Both of one clip's people look like the single person the other clip caught, so a chain of
    # resemblances would make all three one. They cannot be: the first clip filmed two at once.
    people = find_people([
        _track("a1", "clip-a", "a black t-shirt and blue jeans", 0, 30),
        _track("a2", "clip-a", "a black t-shirt and blue jeans", 0, 30),
        _track("b1", "clip-b", "a black t-shirt and blue jeans", 0, 30),
    ])  # fmt: skip
    assert len(people) == 2  # one of a1/a2 is joined to b1; the other stays on its own
    assert sorted(len(p.tracklets) for p in people) == [1, 2]
    for person in people:
        assert len({t.clip_id for t in person.tracklets}) == len(person.tracklets)


def test_one_angle_is_never_matched_to_two_stretches_of_another():
    # A clip's people are matched to another clip's one to one, so a person who arrived here as
    # two separate stretches can only give one of them to the other angle. This is why link_clip
    # takes somebody up again when they come back into frame: a split person cannot be matched.
    people = find_people([
        _track("a1", "clip-a", "a red sleeveless top", 0, 30),
        _track("a2", "clip-a", "a red sleeveless top", 200, 230),
        _track("b1", "clip-b", "red sleeveless top and jeans", 0, 230),
    ])  # fmt: skip
    assert sorted(len(person.tracklets) for person in people) == [1, 2]
    joined = next(person for person in people if len(person.tracklets) > 1)
    assert joined.clips == ["clip-a", "clip-b"]


def test_how_varied_an_events_descriptions_are_is_measured():
    varied = describing(
        [
            _seen(0, "a red sleeveless top"),
            _seen(0, "white long-sleeve shirt"),
            _seen(10, "a green jacket"),
            _seen(10, "yellow top, blue jeans"),
        ],
        windows=2,
    )
    assert varied.outfits == 4
    assert varied.per_window == 2.0
    assert varied.commonest_share == 0.25
    assert not varied.worth_doubting


def test_one_description_over_and_over_is_flagged_as_worth_doubting():
    # What a stadium crowd at night produces: the model stops describing people and repeats one
    # plausible concert-goer, and the repeats match each other across angles perfectly well.
    stock = describing(
        [_seen(t * 10, "red sleeveless top, black trousers") for t in range(9)]
        + [_seen(0, "a green jacket")],
        windows=9,
    )
    assert stock.commonest_share == 0.9
    assert stock.commonest == "red sleeveless top, black trousers"
    assert stock.worth_doubting


def test_descriptions_that_pick_out_nobody_do_not_count_towards_the_variety():
    told = describing([_seen(0, "a person"), _seen(0, "dark clothing"), _seen(0, "a red top")], 1)
    assert told.sightings == 3  # what the model said
    assert told.usable == 1  # what any of it was worth
    assert told.outfits == 1


def test_an_event_nobody_could_be_made_out_at_is_not_a_crash():
    told = describing([], windows=0)
    assert told.outfits == 0 and told.per_window == 0.0
    assert not told.worth_doubting
