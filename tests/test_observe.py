"""Watching clips: the windows, the caching, and what happens when a model misbehaves."""

import json

import numpy as np
import pytest
import synth
from conftest import needs_ffmpeg, run_ffmpeg

from scenefold import observe
from scenefold.ingest import ingest
from scenefold.observations import (
    WatchSettings,
    load_observations,
    observations_path,
)
from scenefold.observe import Ollama, WatchError, observe_event, watch_clip

SETTINGS = WatchSettings(window_s=4.0, frames_per_window=2, model="pretend")


class FakeWatcher:
    """A model that always answers, and counts how often it was asked."""

    name = "pretend"

    def __init__(self, answer=None):
        self.asked: list[int] = []
        self.answer = answer or (lambda n: {"summary": f"window {n}", "subjects": ["a", "b"]})

    def describe(self, pictures):
        self.asked.append(len(pictures))
        return self.answer(len(self.asked))


@pytest.fixture(scope="module")
def event(tmp_path_factory):
    """A tiny ingested event: two drawn clips, no people, nothing uploaded anywhere."""
    folder = tmp_path_factory.mktemp("watch") / "clips"
    folder.mkdir()
    for name, seconds, colour in (("one.mp4", 12, "navy"), ("two.mp4", 8, "olive")):
        wav = synth.write_wav(
            folder / f"{name}.wav", synth.record(synth.scene(20, seed=2), synth.Phone(0.0, seconds))
        )
        run_ffmpeg(f"-v error -f lavfi -i color=c={colour}:s=320x180:r=30:d={seconds}", "-i", wav,
                   "-c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest",
                   folder / name)  # fmt: skip
        wav.unlink()
    data_dir = folder.parent / "data"
    ingest("watch-me", folder, data_dir=data_dir)
    return data_dir


@needs_ffmpeg
def test_a_clip_is_watched_window_by_window(event):
    watcher = FakeWatcher()

    seen = observe_event("watch-me", data_dir=event, settings=SETTINGS, watcher=watcher)

    first = next(clip for clip in seen if clip.name == "one.mp4")
    assert [round(o.t_start_s) for o in first.observations] == [0, 4, 8]  # 12 s in 4 s windows
    assert first.observations[0].t_end_s == pytest.approx(4, abs=0.2)
    assert all(o.summary for o in first.observations)
    assert watcher.asked[0] == SETTINGS.frames_per_window
    assert first.settings.model == "pretend"


@needs_ffmpeg
def test_watching_again_costs_nothing(event):
    observe_event("watch-me", data_dir=event, settings=SETTINGS, watcher=FakeWatcher())
    again = FakeWatcher()

    seen = observe_event("watch-me", data_dir=event, settings=SETTINGS, watcher=again)

    assert again.asked == []  # not one question asked the second time
    assert all(clip.observations for clip in seen)


@needs_ffmpeg
def test_a_different_question_is_asked_again(event):
    observe_event("watch-me", data_dir=event, settings=SETTINGS, watcher=FakeWatcher())
    changed = SETTINGS.model_copy(update={"prompt_version": SETTINGS.prompt_version + 1})
    watcher = FakeWatcher()

    observe_event("watch-me", data_dir=event, settings=changed, watcher=watcher)

    assert watcher.asked  # the answers belonged to the old question, so they were asked again


@needs_ffmpeg
def test_what_was_seen_is_written_down_and_read_back(event):
    seen = observe_event("watch-me", data_dir=event, settings=SETTINGS, watcher=FakeWatcher())

    clip = seen[0]
    path = observations_path(event / "watch-me", clip.clip_id)
    assert path.is_file()
    assert load_observations(event / "watch-me", clip.clip_id) == clip
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["observations"][0]["t_start_s"] == 0.0
    assert load_observations(event / "watch-me", "nothing") is None


@needs_ffmpeg
def test_each_window_carries_how_good_the_picture_was(event):
    seen = observe_event("watch-me", data_dir=event, settings=SETTINGS, watcher=FakeWatcher())

    scores = [o.picture_score for clip in seen for o in clip.observations]
    assert all(score is not None and 0 <= score <= 1 for score in scores)


@needs_ffmpeg
def test_one_bad_window_does_not_lose_the_clip(event):
    def moody(count):
        if count == 2:
            raise RuntimeError("the model fell over")
        return {"summary": f"window {count}", "subjects": []}

    seen = observe_event(
        "watch-me", data_dir=event, settings=SETTINGS, watcher=FakeWatcher(moody), again=True
    )

    watched = next(clip for clip in seen if clip.name == "one.mp4")
    assert len(watched.observations) == 2  # three windows, the middle one lost
    assert [round(o.t_start_s) for o in watched.observations] == [0, 8]


@needs_ffmpeg
def test_an_answer_with_nothing_in_it_is_not_written_down(event):
    empty = FakeWatcher(lambda n: {"summary": "  ", "subjects": []})

    seen = observe_event("watch-me", data_dir=event, settings=SETTINGS, watcher=empty, again=True)

    assert all(clip.observations == [] for clip in seen)


def test_words_instead_of_a_form_are_still_kept():
    """Small models sometimes answer in prose. Keep what they said rather than nothing."""
    assert observe._read_answer("just some words")["summary"] == "just some words"
    assert observe._read_answer('here you go: {"summary": "a crowd"} hope that helps') == {
        "summary": "a crowd"
    }
    assert observe._read_answer('{"summary": "a stage", "subjects": ["lights"]}')["subjects"] == [
        "lights"
    ]
    assert observe._read_answer("[1, 2]")["summary"] == "[1, 2]"


def test_a_window_score_averages_the_seconds_it_covers():
    scores = np.array([0.2, 0.4, 0.6, 0.8])

    assert observe._score_over(scores, 0, 2) == pytest.approx(0.3)
    assert observe._score_over(scores, 2, 4) == pytest.approx(0.7)
    assert observe._score_over(None, 0, 2) is None
    assert observe._score_over(np.zeros(0), 0, 2) is None


def test_a_missing_event_says_what_to_do(tmp_path):
    with pytest.raises(WatchError, match="ingest"):
        observe_event("nothing-here", data_dir=tmp_path, watcher=FakeWatcher())


def test_a_model_that_is_not_there_says_what_to_do():
    missing = Ollama("qwen3.5:4b", host="http://127.0.0.1:1")  # nothing listens there

    assert "Ollama" in (missing.ready() or "")
    with pytest.raises(WatchError, match="could not reach the model"):
        missing.describe([b"not really a picture"])


@needs_ffmpeg
def test_a_clip_with_no_frames_is_not_a_crash(tmp_path):
    empty = tmp_path / "empty.mp4"
    run_ffmpeg("-v error -f lavfi -i color=c=black:s=64x64:r=30:d=1",
               "-c:v libx264 -preset ultrafast -pix_fmt yuv420p -t 0.01", empty)  # fmt: skip

    seen, took = watch_clip(empty, 0.01, SETTINGS, FakeWatcher())

    assert seen == [] or all(o.summary for o in seen)
    assert took >= 0
