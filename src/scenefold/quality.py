"""Score how good each clip's picture is, second by second: sharp, steady, and well exposed.

No AI and no understanding of what is filmed: this only asks whether a second of footage is worth
watching. Three things are measured on small grey frames sampled a few times a second:

- **sharpness** — how much fine detail the frame holds. A blurred or out-of-focus shot has little.
- **steadiness** — how far the whole picture shifts from frame to frame, found by lining each
  frame up with the one before it. A phone being waved about scores low, and so does a fast pan,
  but a dancer crossing a steady frame does not: only the camera moving shifts everything at once.
- **exposure** — how much of the frame is crushed to black or blown to white, and how far the
  whole frame sits from a middle grey. Filming into stage lights ruins a shot this way.

Each measure is turned into a 0-1 score against the rest of the event, so "good" means better than
the other angles of this event, not better than some fixed idea of a good picture. `cut.py` turns
these into a director's cut.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

from scenefold import media

SAMPLE_FPS = 10.0  # frames a second looked at; slower than this and a shaking camera hides
WIDTH, HEIGHT = 320, 180  # small enough to be quick, large enough that blur still shows
SHIFT_GRID = 4  # frames shrink by this much again (to 80x45) before their movement is measured
SHAKY_PX = 1.5  # a picture sliding this far (of 80 across) between frames halves its steadiness
DARK, BRIGHT = 16, 239  # below and above these, an 8-bit picture keeps no detail
# What matters most in a watchable shot. Sharpness leads: a blurred angle is unwatchable however
# steady it is, while a little movement is normal in hand-held footage.
WEIGHTS = {"sharpness": 0.45, "steadiness": 0.35, "exposure": 0.20}


@dataclass(frozen=True)
class Scored:
    """One clip judged against the rest of the event: everything from 0 to 1, second by second."""

    combined: np.ndarray  # the weighted total, which decides the cut
    measures: dict[str, np.ndarray]  # each measure on its own, to explain why an angle won


@dataclass(frozen=True)
class ClipQuality:
    """One clip's picture, second by second on its own clock."""

    clip_id: str
    sharpness: np.ndarray  # detail in the picture; raw, not yet compared with the other clips
    steadiness: np.ndarray  # 1 when nothing moves, falling as the picture changes
    exposure: np.ndarray  # 1 when nothing is crushed or blown and the frame sits mid-grey

    @property
    def seconds(self) -> int:
        return len(self.sharpness)


def score_clip(clip_id: str, video: Path, fps: float = SAMPLE_FPS) -> ClipQuality:
    """Measure a working copy, one second at a time."""
    detail: list[float] = []
    moved: list[float] = []
    clipped: list[float] = []
    middle: list[float] = []
    previous: np.ndarray | None = None
    for raw in media.grey_frames(video, WIDTH, HEIGHT, fps):
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(HEIGHT, WIDTH).astype(np.float32)
        small = frame.reshape(
            HEIGHT // SHIFT_GRID, SHIFT_GRID, WIDTH // SHIFT_GRID, SHIFT_GRID
        ).mean(axis=(1, 3))
        detail.append(float(ndimage.laplace(frame).var()))
        moved.append(0.0 if previous is None else _shift(previous, small))
        clipped.append(float(((frame <= DARK) | (frame >= BRIGHT)).mean()))
        middle.append(abs(float(frame.mean()) - 128) / 128)
        previous = small

    per_second = max(1, round(fps))
    sharpness = _per_second(np.log1p(detail), per_second)
    steadiness = 1 / (1 + _per_second(np.array(moved), per_second) / SHAKY_PX)
    exposure = np.clip(1 - _per_second(np.array(clipped), per_second) * 2, 0, 1) * np.clip(
        1 - _per_second(np.array(middle), per_second), 0, 1
    )
    return ClipQuality(clip_id, sharpness, steadiness, exposure)


def combine(clips: list[ClipQuality]) -> dict[str, Scored]:
    """Per-clip scores from 0 to 1, judged against the other angles of the same event.

    Each measure is stretched over the range the event actually shows, so the best angle of a
    poorly filmed event still scores well and a cut can always be made. A measure that barely
    varies (every clip equally sharp) is left flat at 0.5, so it cannot decide anything by itself.
    """
    if not clips:
        return {}
    filmed = [clip for clip in clips if clip.seconds]
    spread = {}
    for name in WEIGHTS:
        values = np.concatenate([getattr(clip, name) for clip in filmed]) if filmed else np.zeros(0)
        low, high = np.percentile(values, [5, 95]) if len(values) else (0.0, 1.0)
        spread[name] = (float(low), float(high))
    scores = {}
    for clip in clips:
        total = np.zeros(clip.seconds)
        measures = {}
        for name, weight in WEIGHTS.items():
            low, high = spread[name]
            measure = getattr(clip, name)
            stretched = (
                np.full(clip.seconds, 0.5)
                if high - low < 1e-6
                else np.clip((measure - low) / (high - low), 0, 1)
            )
            measures[name] = stretched
            total += weight * stretched
        scores[clip.clip_id] = Scored(total, measures)
    return scores


def _shift(before: np.ndarray, after: np.ndarray) -> float:
    """How far the whole picture moved between two frames, in pixels of the shrunken frame.

    Phase correlation: two frames of the same scene, one shifted, have the same frequencies with a
    sloped phase difference, which turns into a single spike at the shift. Only what moves
    together shows up, so a subject crossing the frame counts for little and the camera moving
    counts for everything.
    """
    spectrum = np.fft.rfft2(before - before.mean()) * np.conj(np.fft.rfft2(after - after.mean()))
    size = np.abs(spectrum)
    if not size.any():
        return 0.0
    peak = np.fft.irfft2(spectrum / (size + 1e-9), before.shape)
    rows, columns = before.shape
    down, across = np.unravel_index(int(np.argmax(peak)), before.shape)
    # the spike wraps around, so a shift past halfway is really a shift the other way
    return float(np.hypot(min(down, rows - down), min(across, columns - across)))


def _per_second(values: np.ndarray, per_second: int) -> np.ndarray:
    """Average each second's samples; a part-second at the end counts as a second."""
    if not len(values):
        return np.zeros(0)
    whole = len(values) // per_second * per_second
    seconds = list(values[:whole].reshape(-1, per_second).mean(axis=1))
    if len(values) > whole:
        seconds.append(float(values[whole:].mean()))
    return np.array(seconds)
