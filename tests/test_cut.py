"""Choosing the shots: does the cut follow the rules an editor would, and say why?"""

from datetime import UTC, datetime

import numpy as np
import pytest
import synth
from conftest import needs_ffmpeg, run_ffmpeg

from scenefold.cut import (
    BETTER,
    FILM_NAME,
    KEPT_ROLLING,
    ONLY_ANGLE,
    SAW_IT,
    SAW_IT_TOO,
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
from scenefold.interest import measure
from scenefold.knowledge import Event, Evidence
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


LAST_INTEREST = None  # what the most recent with_interest() call weighed, for a test to check
PLACED_FOR_INTEREST: list = []


def with_interest(placements, scores, events, settings: CutSettings = SETTINGS) -> Film:
    """The same, with an event store telling the cut what was happening and who saw it."""
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
    microphone, _ = choose_microphone(placements)
    start_s = microphone.offset_s + (microphone.heard_late_s or 0.0)
    seconds = int(microphone.duration_s)
    found = measure(events, start_s, seconds, [p.clip_id for p in placements])
    global LAST_INTEREST, PLACED_FOR_INTEREST  # noqa: PLW0603 - so a test can look at what it weighed
    LAST_INTEREST, PLACED_FOR_INTEREST = found, placements
    return plan_cut(timeline, scored, clips, settings, interest=found)


def found_at(film: Film, at_s: float) -> float:
    """How much was happening at a second of the film, as the cut weighed it."""
    microphone = next(p for p in PLACED_FOR_INTEREST if p.clip_id == film.audio_clip_id)
    start_s = microphone.offset_s + (microphone.heard_late_s or 0.0)
    return float(LAST_INTEREST.happening[int(at_s - start_s)])


def moment(t_master_s, saw, strength=1.0):
    return Event(
        event_id=f"moment-{t_master_s}",
        t_master_s=t_master_s,
        kind="sound",
        strength=strength,
        recording=2,
        evidence=[
            Evidence(clip_id=c, t_local_s=t_master_s, kind="sound", strength=strength) for c in saw
        ],
    )


def test_the_angle_that_caught_the_moment_wins_it_from_a_prettier_one():
    """The limit this phase set out to fix: a sharp shot of the floor beating the moment itself."""
    placements = [placed("pretty", 0, 40), placed("pointed", 0, 40)]
    scores = {"pretty": np.full(40, 0.62), "pointed": np.full(40, 0.45)}
    # with nothing known, the prettier angle holds the whole film
    assert {shot.clip_id for shot in event(placements, scores).shots} == {"pretty"}
    # but several phones caught something in the middle, and only one angle was pointed at it
    film = with_interest(placements, scores, [moment(float(t), ["pointed"]) for t in range(18, 24)])
    shown = {shot.clip_id for shot in film.shots}
    assert "pointed" in shown
    middle = next(s for s in film.shots if s.start_s <= 20 < s.end_s)
    assert middle.clip_id == "pointed"
    assert middle.reason == SAW_IT


def test_an_angle_still_has_to_be_worth_looking_at():
    """Seeing the moment is worth something, not everything: a hopeless picture still loses."""
    film = with_interest(
        [placed("hopeless", 0, 40), placed("good", 0, 40)],
        {"hopeless": np.full(40, 0.05), "good": np.full(40, 0.95)},
        [moment(float(t), ["hopeless"]) for t in range(18, 24)],
    )
    assert {shot.clip_id for shot in film.shots} == {"good"}


def test_where_two_angles_both_saw_it_the_picture_decides():
    film = with_interest(
        [placed("a", 0, 40), placed("b", 0, 40)],
        {"a": np.full(40, 0.40), "b": np.full(40, 0.85)},
        [moment(float(t), ["a", "b"]) for t in range(18, 24)],
    )
    assert {shot.clip_id for shot in film.shots} == {"b"}


def test_a_shot_won_against_an_angle_that_also_saw_it_says_so():
    film = with_interest(
        [placed("saw-less", 0, 40), placed("saw-more", 0, 40)],
        {"saw-less": np.full(40, 0.5), "saw-more": np.full(40, 0.5)},
        [moment(float(t), ["saw-more"]) for t in range(4, 36)]
        + [moment(float(t), ["saw-less", "saw-more"]) for t in range(4, 36, 4)],
    )
    reasons = {shot.reason for shot in film.shots}
    assert reasons <= {SAW_IT, SAW_IT_TOO, ONLY_ANGLE, KEPT_ROLLING} | set(BETTER.values())
    assert SAW_IT_TOO in reasons or SAW_IT in reasons


def test_an_event_with_no_store_cuts_exactly_as_it_did_before():
    placements = [placed("a", 0, 40), placed("b", 0, 40)]
    scores = {"a": np.concatenate([np.full(20, 0.9), np.full(20, 0.3)]), "b": np.full(40, 0.6)}
    before = event(placements, scores)
    after = with_interest(placements, scores, [])
    assert [(s.clip_id, s.start_s, s.end_s) for s in before.shots] == [
        (s.clip_id, s.start_s, s.end_s) for s in after.shots
    ]


def test_the_film_cuts_before_a_moment_rather_than_across_it():
    """An editor cuts on the quiet in front of a moment. So does this."""
    # 'first' is gently better early and 'second' gently better late, so the cut has to happen
    # somewhere in the middle but nothing forces the exact second. That is when craft decides.
    placements = [placed("first", 0, 60), placed("second", 0, 60)]
    scores = {
        "first": np.concatenate([np.full(30, 0.62), np.full(30, 0.50)]),
        "second": np.concatenate([np.full(30, 0.50), np.full(30, 0.62)]),
    }
    # something big happens right where the cut would otherwise land
    film = with_interest(placements, scores, [moment(30.0, ["first", "second"], strength=1.0)])
    boundaries = [shot.start_s for shot in film.shots[1:]]
    assert boundaries, "the film should still cut somewhere"
    assert all(at != 30.0 for at in boundaries), boundaries
    # and where it does cut, nothing much is happening
    assert all(found_at(film, at) < 0.9 for at in boundaries)
