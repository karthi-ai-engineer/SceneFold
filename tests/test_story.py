"""Telling what happened: does every sentence really point at footage that backs it up?"""

from datetime import UTC, datetime

import pytest

from scenefold.knowledge import NOT_IN_VIEW, Conflict, Event, Evidence
from scenefold.story import (
    Line,
    Story,
    StoryError,
    about,
    answer_question,
    as_moments,
    check_citations,
    load_story,
    save_story,
    worth_telling,
    write_story,
)

WHEN = datetime(2026, 9, 21, tzinfo=UTC)


def happened(number, at, summary="a flash", clips=("a",), strength=1.0, conflict=False):
    return Event(
        event_id=f"e-{number:03d}",
        t_master_s=at,
        kind="picture",
        strength=strength,
        recording=len(clips),
        summary=summary,
        evidence=[
            Evidence(
                clip_id=clip,
                t_local_s=at,
                kind="picture",
                strength=strength,
                summary=summary,
                picture_score=0.6,
            )
            for clip in clips
        ],  # fmt: skip
        conflicts=[Conflict(NOT_IN_VIEW, "one missed it", None, "unresolved")] if conflict else [],
    )


def answers(text):
    """A writer that says what the test wants it to say."""
    return lambda prompt: text


def test_the_moments_told_are_spread_across_the_event():
    """Otherwise the account bunches where the lights flashed most and ignores the rest."""
    crowded = [happened(i, 1.0 + i * 0.1, strength=0.9) for i in range(40)]
    spread = [happened(100 + i, 20.0 + i * 10, strength=0.5) for i in range(6)]

    chosen = worth_telling(crowded + spread, most=8)

    assert len(chosen) <= 8
    assert chosen[-1].t_master_s > 50  # the later part of the event is not lost
    assert chosen == sorted(chosen, key=lambda e: e.t_master_s)


def test_a_short_event_is_told_whole():
    events = [happened(i, i * 5.0) for i in range(3)]

    assert worth_telling(events, most=16) == events


def test_moments_reach_the_writer_numbered_and_timed():
    events = [
        happened(1, 65.2, "confetti falls", clips=("a", "b")),
        happened(2, 90.0, conflict=True),
    ]

    text = as_moments(events)

    assert "[1] 1:05.2 — confetti falls (2 clips caught it)" in text
    assert "[2]" in text and "caught nothing here" in text  # the disagreement is passed on


def test_every_sentence_keeps_the_footage_behind_it():
    events = [happened(1, 10.0, "the lights drop", clips=("a", "b")), happened(2, 40.0, "confetti")]

    lines, dropped = write_story(
        events, "pretend", answers("The lights drop. [1] Then confetti falls. [2]")
    )

    assert [line.text for line in lines] == ["The lights drop. [1]", "Then confetti falls. [1]"]
    assert lines[0].cites == ["e-001"] and lines[0].clips == ["a", "b"]
    assert lines[1].t_master_s == 40.0
    assert dropped == []


def test_a_sentence_citing_nothing_is_thrown_away():
    events = [happened(1, 10.0)]

    lines, dropped = write_story(
        events, "pretend", answers("It was a wonderful night for everyone there.")
    )

    assert lines == []
    assert "points at no moment" in dropped[0]


def test_a_sentence_citing_a_moment_that_does_not_exist_is_thrown_away():
    events = [happened(1, 10.0)]

    lines, dropped = write_story(events, "pretend", answers("Fireworks lit the sky. [7]"))

    assert lines == []
    assert "does not exist" in dropped[0]


def test_one_sentence_written_twice_becomes_one_line():
    """Two moments often share a twelve-second description; the reader needs it once."""
    events = [happened(1, 10.0, "the same view", clips=("a",)),
              happened(2, 16.0, "the same view", clips=("b",))]  # fmt: skip

    lines, _ = write_story(events, "pretend", answers("The same view. [1] The same view. [2]"))

    assert len(lines) == 1  # both moments are told by one sentence, citing each
    assert lines[0].cites == ["e-001", "e-002"]
    assert lines[0].clips == ["a", "b"]


def test_a_disputed_moment_is_marked_as_one():
    events = [happened(1, 10.0, conflict=True)]

    lines, _ = write_story(events, "pretend", answers("Something happened. [1]"))

    assert lines[0].disputed


def test_a_citation_the_store_does_not_have_is_caught():
    story = Story(
        event_id="e",
        created_at=WHEN,
        model="pretend",
        lines=[Line(t_master_s=10.0, text="Something happened.", cites=["e-999"], clips=["a"])],
    )

    wrong = check_citations(story, [happened(1, 10.0)], {"a": (0.0, 60.0)})

    assert "not in the store" in wrong[0]


def test_a_citation_no_clip_was_filming_for_is_caught():
    """The moment exists, but the clip behind it had stopped recording by then."""
    event = happened(1, 500.0, clips=("a",))
    story = Story(
        event_id="e",
        created_at=WHEN,
        model="pretend",
        lines=[Line(t_master_s=500.0, text="Late in the night.", cites=["e-001"], clips=["a"])],
    )

    wrong = check_citations(story, [event], {"a": (0.0, 60.0)})

    assert "no clip was filming" in wrong[0]


def test_citations_that_hold_up_raise_nothing():
    event = happened(1, 30.0, clips=("a",))
    story = Story(
        event_id="e",
        created_at=WHEN,
        model="pretend",
        lines=[Line(t_master_s=30.0, text="A flash.", cites=["e-001"], clips=["a"])],
    )

    assert check_citations(story, [event], {"a": (0.0, 60.0)}) == []


def test_a_story_survives_a_round_trip_through_its_file(tmp_path):
    story = Story(
        event_id="e",
        created_at=WHEN,
        model="pretend",
        lines=[Line(t_master_s=1.0, text="A flash.", cites=["e-001"], clips=["a"])],
        dropped=["something — points at no moment"],
    )

    save_story(tmp_path, story)

    assert load_story(tmp_path) == story
    assert load_story(tmp_path / "nowhere") is None


def test_an_unreadable_story_says_so(tmp_path):
    (tmp_path / "story.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(StoryError, match="unreadable"):
        load_story(tmp_path)


def test_nothing_known_means_nothing_told():
    assert write_story([], "pretend", answers("anything at all [1]")) == ([], [])


def test_a_question_reaches_the_moments_that_mention_it():
    events = [
        happened(1, 10.0, "the crowd holds up lights"),
        happened(2, 50.0, "confetti falls over everyone"),
        happened(3, 90.0, "a dark empty stage"),
    ]

    chosen = about(events, "when did the confetti fall?")

    assert chosen[0].event_id == "e-002"


def test_a_question_matching_nothing_still_gets_the_whole_event():
    """'What happened?' shares no words with anything, and must not therefore get nothing."""
    events = [happened(i, i * 10.0, "a flash") for i in range(1, 6)]

    chosen = about(events, "what happened?")

    assert len(chosen) == 5


def test_an_answer_cites_the_footage_behind_it():
    events = [happened(1, 30.0, "confetti falls", clips=("a", "b"))]

    lines, dropped = answer_question(
        events, "was there confetti?", answers("Yes, confetti fell over the crowd. [1]")
    )

    assert lines[0].text == "Yes, confetti fell over the crowd. [1]"
    assert lines[0].cites == ["e-001"] and lines[0].clips == ["a", "b"]
    assert dropped == []


def test_an_answer_the_footage_cannot_support_is_thrown_away():
    events = [happened(1, 30.0, "confetti falls")]

    lines, dropped = answer_question(
        events, "how many people were there?", answers("About five thousand people were there.")
    )

    assert lines == []
    assert "points at no moment" in dropped[0]


def test_saying_the_footage_does_not_show_it_is_passed_on_as_it_is():
    events = [happened(1, 30.0, "confetti falls")]

    lines, dropped = answer_question(
        events, "was anybody hurt?", answers("The footage does not show this.")
    )

    assert (lines, dropped) == ([], [])  # not an error and not a dropped sentence: an answer


def test_taking_out_several_citations_does_not_leave_punctuation_behind():
    """A model citing a list writes "the footage [3], [4] and [5]": the marks go, the words stay."""
    events = [happened(n, n * 10.0, clips=("a",)) for n in range(1, 6)]

    lines, _ = write_story(
        events,
        "pretend",
        answers("Circular screens showed the performers [1], [2], [3] and [4]."),
    )

    assert lines[0].text == "Circular screens showed the performers [1], [2], [3] and [4]."
    assert lines[0].cites == ["e-001", "e-002", "e-003", "e-004"]


def test_an_answer_that_only_mentions_not_showing_still_counts():
    """Mentioning the phrase is not the same as refusing: the sentence still cites a moment."""
    events = [happened(1, 30.0, "screens showing the stage")]

    lines, _ = answer_question(
        events,
        "what was on the screens?",
        answers("The footage does not show this clearly, but screens are visible [1]."),
    )

    assert len(lines) == 1
    assert lines[0].cites == ["e-001"]
