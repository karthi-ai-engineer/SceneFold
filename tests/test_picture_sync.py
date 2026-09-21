"""Sync end to end on clips whose pictures and sound disagree, because sound takes time to arrive.

The clips are drawn: a lighting curve is filmed by each phone on its own clock, while its sound
comes from its own distance. Sync should place the clips by their sound (as it always has) and
report how much later than the nearest phone each one heard the event.
"""

import subprocess

import numpy as np
import pytest
import synth
from conftest import needs_ffmpeg

from scenefold.ingest import ingest
from scenefold.sync import sync_event

WIDTH, HEIGHT, FPS = 64, 36, 30
SHOW_S = 130.0
# name: where it starts filming, how long it films, how far it stands from the sound
PHONES = {
    "near-stage.mp4": (0.0, 75.0, 0.0),
    "middle.mp4": (18.0, 75.0, 70.0),
    "far-back.mp4": (35.0, 65.0, 120.0),
}
LATE_S = {name: distance / synth.SOUND_M_PER_S for name, (*_, distance) in PHONES.items()}
FRAME_S = 1 / FPS


def write_clip(path, brightness, samples, folder):
    """A video whose every frame is one shade of grey, so its brightness curve is known exactly."""
    wav = synth.write_wav(folder / f"{path.stem}.wav", samples)
    frames = np.repeat(
        np.clip(brightness, 0, 255).astype(np.uint8)[:, None], WIDTH * HEIGHT, axis=1
    )
    done = subprocess.run(
        ["ffmpeg", "-hide_banner", "-v", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS), "-i", "-",
         "-i", str(wav), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(path)],
        input=frames.tobytes(), capture_output=True,
    )  # fmt: skip
    wav.unlink()
    if done.returncode != 0:
        raise RuntimeError(done.stderr.decode("utf-8", errors="replace"))
    return path


def build_event(tmp_path, lighting):
    """One imaginary show, filmed by three phones standing at different distances."""
    folder = tmp_path / "clips"
    folder.mkdir()
    sound = synth.scene(SHOW_S, seed=7)
    for name, (start, seconds, distance) in PHONES.items():
        phone = synth.Phone(
            start_s=start, seconds=seconds, distance_m=distance, seed=abs(hash(name)) % 100
        )
        write_clip(
            folder / name,
            synth.filmed(lighting, phone, fps=FPS),
            synth.record(sound, phone),
            folder,
        )
    data_dir = tmp_path / "data"
    ingest("light-show", folder, data_dir=data_dir)
    return data_dir


@pytest.fixture(scope="module")
def lit_event(tmp_path_factory):
    lighting = synth.lighting(SHOW_S, seed=3, fps=FPS)
    return build_event(tmp_path_factory.mktemp("lit"), lighting)


@pytest.fixture(scope="module")
def lit(lit_event):
    """Synced once: reading the pictures of every clip is the slow part."""
    return sync_event("light-show", data_dir=lit_event)


@pytest.fixture(scope="module")
def steady(tmp_path_factory):
    steady_light = np.full(int(SHOW_S * FPS), 120.0)
    event = build_event(tmp_path_factory.mktemp("steady"), steady_light)
    return sync_event("light-show", data_dir=event)


@needs_ffmpeg
def test_how_late_each_phone_heard_the_event_is_measured(lit):
    placed = {c.name: c for c in lit.clips if c.placed}
    assert set(placed) == set(PHONES)
    nearest = min(LATE_S.values())
    for name, late in LATE_S.items():
        assert placed[name].heard_late_s == pytest.approx(late - nearest, abs=FRAME_S), name


@needs_ffmpeg
def test_the_offsets_still_line_up_the_sound(lit):
    """Sound alignment is unchanged; adding heard_late_s lines the pictures up instead."""
    placed = {c.name: c for c in lit.clips if c.placed}
    first = min(placed.values(), key=lambda c: c.offset_s)
    heard_first = PHONES[first.name][0] - LATE_S[first.name]
    for name, (start, _, _) in PHONES.items():
        heard = start - LATE_S[name]
        assert placed[name].offset_s == pytest.approx(heard - heard_first, abs=0.01), name
        pictures = placed[name].offset_s + placed[name].heard_late_s
        assert pictures == pytest.approx(start - PHONES[first.name][0], abs=FRAME_S), name


@needs_ffmpeg
def test_every_picture_match_is_written_down(lit):
    matched = [p for p in lit.pairs if p.picture_lag_s is not None]
    # a pair whose match does not lead every other lag is set aside, so not all three need land
    assert len(matched) >= 2
    late = {c.clip_id: c.heard_late_s for c in lit.clips if c.placed}
    for pair in matched:
        assert pair.picture_clearness > 4
        assert pair.picture_used
        # each row's two answers must describe the same direction: A then B, as the row reads
        assert pair.picture_lag_s == pytest.approx(pair.lag_s, abs=0.5)
        wanted = (late[pair.clip_b] - late[pair.clip_a]) * 1000
        assert pair.picture_difference_ms == pytest.approx(wanted, abs=FRAME_S * 1000)


@needs_ffmpeg
def test_steady_light_says_it_cannot_tell(steady):
    """A steadily lit room gives no answer, and that is reported rather than guessed."""
    placed = [c for c in steady.clips if c.placed]
    assert len(placed) == 3  # the sound still places them
    assert all(c.heard_late_s is None for c in placed)
    assert not any(p.picture_used for p in steady.pairs)


@needs_ffmpeg
def test_pictures_can_be_left_out(lit_event):
    timeline = sync_event("light-show", data_dir=lit_event, pictures=False)

    assert all(c.heard_late_s is None for c in timeline.clips)
    assert all(p.picture_lag_s is None for p in timeline.pairs)
