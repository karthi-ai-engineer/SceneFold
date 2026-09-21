"""Choosing the shots: does the cut follow the rules an editor would, and say why?"""

from datetime import UTC, datetime

import numpy as np
import pytest
import synth
from conftest import needs_ffmpeg, run_ffmpeg

from scenefold.cut import (
    FILM_NAME,
    KEPT_ROLLING,
    ONLY_ANGLE,
    CutError,
    CutSettings,
    Film,
    choose_microphone,
    cut_event,
    load_cut,
    plan_cut,
    save_cut,
)
from scenefold.ingest import ingest
from scenefold.manifest import Clip, ClipStatus, Proxy, Source
from scenefold.media import probe
from scenefold.quality import Scored
from scenefold.sync import sync_event
from scenefold.timeline import ClipPlacement, SyncSettings, Timeline

WHEN = datetime(2026, 9, 21, tzinfo=UTC)
SETTINGS = CutSettings()


def clip(clip_id: str, seconds: float) -> Clip:
    return Clip(
        clip_id=clip_id,
        status=ClipStatus.OK,
        ingested_at=WHEN,
        source=Source(
            name=f"{clip_id}.mp4", path=f"/in/{clip_id}.mp4", size_bytes=1, modified_ns=1, sha256=""
        ),
        proxy=Proxy(
            video=f"proxies/{clip_id}.mp4",
            audio=f"proxies/{clip_id}.wav",
            width=1280,
            height=720,
            fps=30,
            duration_s=seconds,
            settings_key="k",
        ),
    )


def placed(clip_id: str, offset_s: float, seconds: float, late_s: float | None = None):
    return ClipPlacement(
        clip_id=clip_id,
        name=f"{clip_id}.mp4",
        placed=True,
        offset_s=offset_s,
        duration_s=seconds,
        heard_late_s=late_s,
        confidence=5.0,
    )


def event(placements, scores: dict[str, np.ndarray], settings: CutSettings = SETTINGS) -> Film:
    """Plan a cut from made-up per-second scores, one value a second on each clip's own clock."""
    timeline = Timeline(
        event_id="made-up",
        created_at=WHEN,
        settings=SyncSettings(),
        duration_s=max(p.offset_s + p.duration_s for p in placements),
        clips=placements,
        pairs=[],
    )
    clips = {p.clip_id: clip(p.clip_id, p.duration_s) for p in placements}
    scored = {
        clip_id: Scored(values, {name: values for name in ("sharpness", "steadiness", "exposure")})
        for clip_id, values in scores.items()
    }
    return plan_cut(timeline, scored, clips, settings)


def lengths(film: Film) -> list[float]:
    return [round(shot.end_s - shot.start_s, 3) for shot in film.shots]


def test_the_film_runs_from_end_to_end_of_the_microphone_clip():
    film = event(
        [placed("a", 0, 30), placed("b", 10, 15)],
        {"a": np.full(30, 0.5), "b": np.full(15, 0.9)},
    )

    assert film.duration_s == 30
    assert film.start_s == 0
    assert film.shots[0].start_s == 0
    assert film.shots[-1].end_s == 30
    assert sum(lengths(film)) == 30
    for before, after in zip(film.shots, film.shots[1:], strict=False):
        assert after.start_s == before.end_s  # no gap, no overlap


def test_the_sound_comes_from_the_longest_clip():
    microphone, reason = choose_microphone([placed("a", 0, 30), placed("b", 5, 90)])

    assert microphone.clip_id == "b"
    assert "longest" in reason


def test_of_equally_long_clips_the_nearest_to_the_sound_is_the_microphone():
    """Its sound is the least delayed, so it fits pictures that show the event itself."""
    clips = [placed("far", 0, 60, late_s=0.3), placed("near", 5, 60, late_s=0.0)]

    microphone, reason = choose_microphone(clips)

    assert microphone.clip_id == "near"
    assert "nearest" in reason


def test_a_clearly_better_angle_is_cut_to():
    film = event(
        [placed("dull", 0, 40), placed("great", 0, 40)],
        {"dull": np.full(40, 0.2), "great": np.full(40, 0.9)},
    )

    assert {shot.clip_id for shot in film.shots} == {"great"}


def test_a_slightly_better_angle_is_not_worth_a_cut():
    """A cut costs the viewer something, so it has to buy more than a rounding error."""
    film = event(
        [placed("a", 0, 40), placed("b", 0, 40)],
        {"a": np.concatenate([np.full(20, 0.60), np.full(20, 0.58)]), "b": np.full(40, 0.62)},
    )

    assert len(film.shots) == 1


def test_no_shot_is_shorter_than_the_minimum():
    rng = np.random.default_rng(3)
    film = event(
        [placed("a", 0, 60), placed("b", 0, 60), placed("c", 0, 60)],
        {name: rng.uniform(0, 1, 60) for name in "abc"},
    )

    assert len(film.shots) > 1  # noise this strong is worth cutting for
    assert min(lengths(film)) >= SETTINGS.min_shot_s


def test_faced_with_equals_it_moves_on_rather_than_back():
    """a, then b for a while. At the end a and c look the same, so going back to a would be
    restless for nothing: a third angle costs the viewer no more and shows something new."""
    scores = {
        "a": np.concatenate([np.full(15, 0.90), np.full(25, 0.60)]),
        "b": np.concatenate([np.full(15, 0.10), np.full(10, 0.95), np.full(15, 0.10)]),
        "c": np.concatenate([np.full(25, 0.50), np.full(15, 0.60)]),
    }

    film = event([placed(name, 0, 40) for name in "abc"], scores)

    assert [shot.clip_id for shot in film.shots] == ["a", "b", "c"]


def test_one_angle_alone_is_held_however_long_that_takes():
    """Nothing to cut to: a stretch with one camera must still be covered."""
    film = event(
        [placed("alone", 0, 60), placed("late", 40, 20)],
        {"alone": np.full(60, 0.5), "late": np.full(20, 0.9)},
    )

    assert film.shots[0].clip_id == "alone"
    assert film.shots[0].end_s >= 40  # held all the way to where the second angle begins
    assert film.shots[0].reason == ONLY_ANGLE


def test_every_shot_says_why_it_was_chosen():
    film = event(
        [placed("a", 0, 40), placed("b", 0, 40)],
        {"a": np.full(40, 0.9), "b": np.full(40, 0.2)},
    )

    for shot in film.shots:
        assert shot.reason
        assert shot.score == pytest.approx(0.9, abs=0.01)
    assert "sharp" in film.shots[0].reason or "steadi" in film.shots[0].reason


def test_an_angle_kept_although_another_looks_better_says_so():
    """b is a hair better once it joins, but not by enough to pay for a cut."""
    film = event(
        [placed("a", 0, 40), placed("b", 10, 30)],
        {"a": np.full(40, 0.60), "b": np.full(30, 0.61)},
    )

    assert [shot.clip_id for shot in film.shots] == ["a"]
    assert film.shots[0].reason == KEPT_ROLLING


def test_shots_point_at_the_right_moment_of_each_clip():
    """A clip that started late, ran fast, and stood far away still lands on the right frame."""
    film = event(
        [placed("mic", 0, 40), placed("other", 12.5, 30, late_s=0.2)],
        {"mic": np.full(40, 0.1), "other": np.full(30, 0.9)},
    )

    shot = next(s for s in film.shots if s.clip_id == "other")
    assert shot.start_s >= 12.7  # never before the clip's first frame
    assert shot.local_start_s == pytest.approx(shot.start_s - 12.7, abs=0.01)
    assert shot.local_end_s == pytest.approx(shot.end_s - 12.7, abs=0.01)


def test_a_film_shorter_than_one_shot_still_shows_something():
    film = event(
        [placed("a", 0, 2), placed("b", 0, 2)], {"a": np.full(2, 0.2), "b": np.full(2, 0.8)}
    )

    assert [shot.clip_id for shot in film.shots] == ["b"]
    assert film.duration_s == 2


def test_nothing_placed_is_an_error():
    timeline = Timeline(
        event_id="empty", created_at=WHEN, settings=SyncSettings(), duration_s=0, clips=[], pairs=[]
    )

    with pytest.raises(CutError, match="on the clock"):
        plan_cut(timeline, {}, {})


def test_the_cut_survives_a_round_trip_through_its_file(tmp_path):
    film = event([placed("a", 0, 20)], {"a": np.full(20, 0.5)})

    save_cut(tmp_path, film)

    assert load_cut(tmp_path) == film
    assert load_cut(tmp_path / "nowhere") is None


@needs_ffmpeg
def test_a_film_comes_out_the_other_end(tmp_path):
    """From two ingested clips to a playable film: shots on the right frames, sound unbroken."""
    folder = tmp_path / "clips"
    folder.mkdir()
    sound = synth.scene(30, seed=5)
    for name, filters, phone in (
        ("sharp.mp4", "null", synth.Phone(0.0, 24, seed=1)),
        ("blurred.mp4", "gblur=sigma=6", synth.Phone(6.0, 20, seed=2)),
    ):
        wav = synth.write_wav(folder / f"{name}.wav", synth.record(sound, phone))
        run_ffmpeg(f"-v error -f lavfi -i testsrc2=s=320x180:r=30:d={phone.seconds}",
                   "-i", wav, "-vf", filters,
                   "-c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest",
                   folder / name)  # fmt: skip
        wav.unlink()
    data_dir = tmp_path / "data"
    ingest("cut-me", folder, data_dir=data_dir)
    sync_event("cut-me", data_dir=data_dir, pictures=False)

    film = cut_event("cut-me", data_dir=data_dir)

    assert [shot.clip_id for shot in film.shots]  # something was chosen
    assert {shot.name for shot in film.shots} == {"sharp.mp4"}  # the blurred angle never wins
    made = data_dir / "cut-me" / FILM_NAME
    assert made.is_file()
    info = probe(made)
    assert info.duration_s == pytest.approx(film.duration_s, abs=0.5)
    assert info.video is not None and info.audio is not None
    assert (info.video.width, info.video.height) == (film.settings.width, film.settings.height)


@needs_ffmpeg
def test_the_film_can_be_planned_without_rendering(tmp_path):
    folder = tmp_path / "clips"
    folder.mkdir()
    wav = synth.write_wav(
        folder / "only.wav", synth.record(synth.scene(20, seed=6), synth.Phone(0.0, 15))
    )
    run_ffmpeg("-v error -f lavfi -i testsrc2=s=320x180:r=30:d=15", "-i", wav,
               "-c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest",
               folder / "only.mp4")  # fmt: skip
    wav.unlink()
    data_dir = tmp_path / "data"
    ingest("plan-me", folder, data_dir=data_dir)
    sync_event("plan-me", data_dir=data_dir, pictures=False)

    film = cut_event("plan-me", data_dir=data_dir, render=False)

    assert load_cut(data_dir / "plan-me") == film
    assert not (data_dir / "plan-me" / FILM_NAME).exists()
    assert film.shots[0].reason == ONLY_ANGLE
