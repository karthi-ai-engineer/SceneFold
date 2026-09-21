"""Watching clips: the windows, the caching, and what happens when a model misbehaves."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import synth
from conftest import needs_ffmpeg, run_ffmpeg

from scenefold import observe
from scenefold import speech as speech_module
from scenefold.ingest import ingest
from scenefold.observations import (
    SpeechSettings,
    Utterance,
    WatchSettings,
    Word,
    load_observations,
    observations_path,
)
from scenefold.observe import Ollama, WatchError, observe_event, watch_clip
from scenefold.speech import FasterWhisper, SpeechError, listen_to_clip

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


class FakeEars:
    """Something that hears, without a Whisper model anywhere near the tests."""

    name = "pretend ears"

    def __init__(self):
        self.heard: list[Path] = []

    def hear(self, wav, settings):
        self.heard.append(wav)
        return [
            Utterance(
                t_start_s=1.0,
                t_end_s=2.5,
                text="hello everyone",
                words=[
                    Word(word="hello", t_start_s=1.0, t_end_s=1.6, sureness=0.9),
                    Word(word="everyone", t_start_s=1.7, t_end_s=2.5, sureness=0.8),
                ],
                language="en",
            )
        ]


@needs_ffmpeg
def test_what_was_said_is_written_beside_what_was_seen(event):
    ears = FakeEars()

    seen = observe_event(
        "watch-me",
        data_dir=event,
        settings=SETTINGS,
        watcher=FakeWatcher(),
        speech=SpeechSettings(model="pretend"),
        transcriber=ears,
        again=True,
    )

    clip = seen[0]
    assert len(ears.heard) == 2  # both clips listened to, each on its own
    assert clip.speech[0].text == "hello everyone"
    assert [w.word for w in clip.speech[0].words] == ["hello", "everyone"]
    assert clip.speech_settings.model == "pretend"
    assert clip.speech_seconds_taken is not None
    assert load_observations(event / "watch-me", clip.clip_id).speech == clip.speech


@needs_ffmpeg
def test_listening_again_costs_nothing(event):
    speech = SpeechSettings(model="pretend")
    observe_event("watch-me", data_dir=event, settings=SETTINGS, watcher=FakeWatcher(),
                  speech=speech, transcriber=FakeEars(), again=True)  # fmt: skip
    ears = FakeEars()

    observe_event("watch-me", data_dir=event, settings=SETTINGS, watcher=FakeWatcher(),
                  speech=speech, transcriber=ears)  # fmt: skip

    assert ears.heard == []


@needs_ffmpeg
def test_watching_again_keeps_what_was_already_heard(event):
    """The pictures and the sound are asked of different models: one must not wipe the other."""
    speech = SpeechSettings(model="pretend")
    observe_event("watch-me", data_dir=event, settings=SETTINGS, watcher=FakeWatcher(),
                  speech=speech, transcriber=FakeEars(), again=True)  # fmt: skip

    seen = observe_event(
        "watch-me", data_dir=event, settings=SETTINGS, watcher=FakeWatcher(), again=True
    )

    assert all(clip.speech for clip in seen)
    assert all(clip.speech_settings is not None for clip in seen)


def test_an_utterance_with_no_words_is_dropped():
    """Whisper sometimes returns a segment with text but no timed words: it cannot be placed."""

    class Piece:
        start, end, text, words = 0.0, 1.0, "mm", []

    assert speech_module._utterance(Piece(), "en") is None


def test_words_keep_their_own_times():
    class Timed:
        def __init__(self, word, start, end, probability=None):
            self.word, self.start, self.end, self.probability = word, start, end, probability

    class Piece:
        start, end, text = 4.0, 5.2, " Right, here we go "
        words = [Timed(" Right,", 4.0, 4.4, 0.91), Timed(" here", 4.5, 4.8), Timed(" ", 4.9, 5.0)]

    said = speech_module._utterance(Piece(), "en")

    assert said.text == "Right, here we go"  # the engine pads its text with spaces
    assert [w.word for w in said.words] == ["Right,", "here"]  # the empty one is not a word
    assert said.words[0].sureness == 0.91 and said.words[1].sureness is None
    assert said.t_start_s == 4.0 and said.words[1].t_end_s == 4.8


def test_listening_needs_the_sound_file(tmp_path):
    with pytest.raises(SpeechError, match="missing"):
        listen_to_clip(tmp_path / "gone.wav", SpeechSettings(), FakeEars())


def test_without_the_engine_it_says_how_to_get_it(monkeypatch):
    ears = FasterWhisper("small")
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper", None)

    with pytest.raises(SpeechError, match="uv sync --extra speech"):
        ears.load()


@pytest.mark.skipif(sys.platform != "win32", reason="makes its test speech with Windows' own voice")
def test_the_real_engine_hears_real_words(tmp_path):
    """End to end with Whisper itself: say known words, check they come back with times.

    Skipped unless the speech extra is installed (`uv sync --extra speech`), so CI and a plain
    checkout are unaffected.
    """
    pytest.importorskip("faster_whisper")
    spoken = "the show starts in five minutes"
    wav = tmp_path / "said.wav"
    made = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Add-Type -AssemblyName System.Speech;"
         "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
         f"$s.SetOutputToWaveFile('{wav}'); $s.Speak('{spoken}'); $s.Dispose()"],
        capture_output=True,
    )  # fmt: skip
    if made.returncode != 0 or not wav.is_file():
        pytest.skip("this machine has no voice to test with")

    heard, took = listen_to_clip(wav, SpeechSettings(model="tiny"), FasterWhisper("tiny"))

    words = [w.word.strip(" ,.").lower() for u in heard for w in u.words]
    assert "show" in words and "minutes" in words
    assert all(u.t_start_s <= u.t_end_s for u in heard)
    assert all(w.t_start_s < w.t_end_s for u in heard for w in u.words)  # every word is timed
    assert took > 0


@needs_ffmpeg
def test_descriptions_are_pulled_onto_the_moment_they_describe(event):
    """A window says what happened; the sound says when, far more precisely."""
    seen = observe_event(
        "watch-me", data_dir=event, settings=SETTINGS, watcher=FakeWatcher(), again=True
    )

    clip = next(c for c in seen if c.name == "one.mp4")
    assert clip.moments, "the drawn clips have claps in their sound"
    assert all(m.kind in ("sound", "picture") for m in clip.moments)
    assert all(0 <= m.strength <= 1 for m in clip.moments)
    timed = [o for o in clip.observations if o.at_s is not None]
    assert timed, "at least one window holds a moment"
    for one in timed:
        assert one.t_start_s <= one.at_s < one.t_end_s  # the moment sits inside its own window
        assert one.at_kind in ("sound", "picture")


@needs_ffmpeg
def test_a_spoken_line_is_placed_on_the_sound_that_starts_it(event):
    class OneLine:
        name = "pretend ears"

        def hear(self, wav, settings):
            return [Utterance(t_start_s=0.15, t_end_s=1.0, text="off by a little", words=[
                Word(word="off", t_start_s=0.15, t_end_s=0.4)])]  # fmt: skip

    seen = observe_event(
        "watch-me",
        data_dir=event,
        settings=SETTINGS,
        watcher=FakeWatcher(),
        speech=SpeechSettings(model="pretend"),
        transcriber=OneLine(),
        again=True,
    )

    clip = next(c for c in seen if c.moments)
    near = [m for m in clip.moments if abs(m.t_s - 0.15) <= 0.25]
    said = clip.speech[0]
    assert said.at_s == (max(near, key=lambda m: m.strength).t_s if near else None)
