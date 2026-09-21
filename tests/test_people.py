"""Asking a clip who is in it: the windows, the caching, and what is thrown away.

No model is run here. A pretend one answers with the shapes a real one produces, including the
unhelpful ones — a crowd, a refusal, a paragraph instead of a person.
"""

import pytest
import synth
from conftest import needs_ffmpeg, run_ffmpeg

from scenefold.ingest import ingest
from scenefold.observations import (
    PeopleSettings,
    WatchSettings,
    load_observations,
)
from scenefold.observe import WatchError, observe_event
from scenefold.people import see_people, watch_people

SETTINGS = PeopleSettings(window_s=4.0, model="pretend")
WATCHING = WatchSettings(window_s=4.0, frames_per_window=2, model="pretend")


class FakeWatcher:
    """Answers whatever it was told to, and remembers being asked."""

    name = "pretend"

    def __init__(self, answer=None):
        self.asked = 0
        self.answer = answer or (lambda n: {"summary": f"window {n}", "subjects": []})

    def describe(self, pictures):
        self.asked += 1
        return self.answer(self.asked)

    def ask(self, pictures, prompt, shape, warmth=0.2):
        self.asked += 1
        return self.answer(self.asked)


def _crowd(_):
    return {
        "people": [
            {"wearing": "a red sleeveless top", "doing": "dancing", "where": "centre"},
            {"wearing": "white long-sleeve shirt", "doing": "playing guitar", "where": "left"},
        ]
    }


@pytest.fixture(scope="module")
def event(tmp_path_factory):
    """A tiny ingested event of two drawn clips. Nobody is in them; a pretend model says so."""
    folder = tmp_path_factory.mktemp("who") / "clips"
    folder.mkdir()
    for name, seconds, colour in (("one.mp4", 12, "navy"), ("two.mp4", 8, "olive")):
        wav = synth.write_wav(
            folder / f"{name}.wav", synth.record(synth.scene(20, seed=5), synth.Phone(0.0, seconds))
        )
        run_ffmpeg(f"-v error -f lavfi -i color=c={colour}:s=320x180:r=30:d={seconds}", "-i", wav,
                   "-c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest",
                   folder / name)  # fmt: skip
        wav.unlink()
    data_dir = folder.parent / "data"
    ingest("who-was-there", folder, data_dir=data_dir)
    observe_event("who-was-there", data_dir=data_dir, settings=WATCHING, watcher=FakeWatcher())
    return data_dir


@needs_ffmpeg
def test_every_window_of_a_clip_is_asked_about(event):
    asked = watch_people(
        "who-was-there", data_dir=event, settings=SETTINGS, looker=FakeWatcher(_crowd)
    )
    by_name = {clip.name: clip for clip in asked}
    # 12 seconds in windows of 4, two people seen in each
    assert len(by_name["one.mp4"].people) == 6
    assert len(by_name["two.mp4"].people) == 4
    first = by_name["one.mp4"].people[0]
    assert first.wearing == "a red sleeveless top"
    assert first.doing == "dancing"
    assert (first.t_start_s, first.t_end_s) == (0.0, 4.0)
    assert by_name["one.mp4"].people_seconds_taken is not None


@needs_ffmpeg
def test_asking_the_same_question_twice_costs_nothing(event):
    watch_people("who-was-there", data_dir=event, settings=SETTINGS, looker=FakeWatcher(_crowd))
    again = FakeWatcher(_crowd)
    watch_people("who-was-there", data_dir=event, settings=SETTINGS, looker=again)
    assert again.asked == 0
    # ...unless the question changed
    louder = FakeWatcher(_crowd)
    watch_people(
        "who-was-there",
        data_dir=event,
        settings=SETTINGS.model_copy(update={"most": 3}),
        looker=louder,
    )
    assert louder.asked > 0


@needs_ffmpeg
def test_what_was_seen_and_heard_before_is_not_lost(event):
    watch_people("who-was-there", data_dir=event, settings=SETTINGS, looker=FakeWatcher(_crowd))
    done = load_observations(event / "who-was-there", _a_clip_id(event))
    assert done.observations  # the descriptions from `observe` are still there
    assert done.settings.model == "pretend"
    assert done.people


@needs_ffmpeg
def test_a_clip_nobody_has_watched_is_refused(tmp_path):
    folder = tmp_path / "clips"
    folder.mkdir()
    run_ffmpeg("-v error -f lavfi -i color=c=black:s=320x180:r=30:d=4",
               "-c:v libx264 -preset ultrafast -pix_fmt yuv420p",
               folder / "lonely.mp4")  # fmt: skip
    data_dir = tmp_path / "data"
    ingest("unwatched", folder, data_dir=data_dir)
    with pytest.raises(WatchError, match="has not been watched yet"):
        watch_people("unwatched", data_dir=data_dir, settings=SETTINGS, looker=FakeWatcher(_crowd))


@needs_ffmpeg
def test_only_so_many_people_are_taken_from_one_frame(event):
    def a_stadium(_):
        return {
            "people": [
                {"wearing": f"a {colour} shirt"}
                for colour in ("red", "blue", "green", "yellow", "purple", "orange", "pink")
            ]
        }

    settings = SETTINGS.model_copy(update={"most": 2, "prompt_version": 9})
    asked = watch_people(
        "who-was-there", data_dir=event, settings=settings, looker=FakeWatcher(a_stadium)
    )
    windows = {(s.t_start_s, s.t_end_s) for s in asked[0].people}
    assert len(asked[0].people) == 2 * len(windows)


@needs_ffmpeg
def test_descriptions_that_pick_out_nobody_are_dropped(event, tmp_path):
    def vague(_):
        return {
            "people": [
                {"wearing": "a person"},
                {"wearing": "dark clothing"},
                {"wearing": ""},
                {"wearing": "a green jacket"},  # the only one worth keeping
            ]
        }

    video = next((event / "who-was-there" / "proxies").glob("*.mp4"))
    found, _ = see_people(video, 8.0, SETTINGS, FakeWatcher(vague))
    assert found
    assert {s.wearing for s in found} == {"a green jacket"}


@needs_ffmpeg
def test_a_window_the_model_fails_on_does_not_lose_the_clip(event):
    def sometimes(n):
        if n == 2:
            raise ValueError("the model said something unreadable")
        return _crowd(n)

    video = next((event / "who-was-there" / "proxies").glob("*.mp4"))
    found, _ = see_people(video, 12.0, SETTINGS, FakeWatcher(sometimes))
    assert len(found) == 4  # three windows, one of them lost, two people in each of the rest
    assert {s.t_start_s for s in found} == {0.0, 8.0}


@needs_ffmpeg
def test_an_answer_with_nobody_in_it_is_an_answer(event):
    video = next((event / "who-was-there" / "proxies").glob("*.mp4"))
    found, took = see_people(video, 8.0, SETTINGS, FakeWatcher(lambda _: {"people": []}))
    assert found == []
    assert took >= 0


def _a_clip_id(data_dir):
    import json

    manifest = json.loads((data_dir / "who-was-there" / "manifest.json").read_text())
    return next(c["clip_id"] for c in manifest["clips"] if c["source"]["name"] == "one.mp4")
