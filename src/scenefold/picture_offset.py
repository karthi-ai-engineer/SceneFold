"""Measure how far apart two clips are from their pictures, and how far each phone stood away.

Sync listens, so it lines up the moment each phone *heard* the event. Sound needs about 2.9 ms to
travel a metre, so a phone further from the speakers ends up with its picture ahead of everybody
else's by its extra distance: its flash comes first on the shared clock, by 423 ms across one
stadium concert. This module reads how bright each working copy is frame by frame (stage lighting,
flashes), matches those curves around where the sound put each pair, and turns the differences into
one number per clip: how much later than the nearest phone it heard the event.

    t_master of this clip's pictures = offset_s + heard_late_s + t_local / (1 + drift_ppm / 1e6)

Brightness only works where something visibly changes together. Stage lighting is ideal; a steadily
lit room gives no answer at all, and then this says so instead of guessing. Three traps, all
measured. A match has to be judged against lags far away, because brightness changes slowly and
nearby lags score almost as well. Anything that repeats matches at every repeat, so the match must
also be the best one anywhere, not only the best where the sound said to look: video encoding
leaves its mark on every keyframe, a second apart, and stage lighting follows the beat. And
single-frame flicker does not survive encoding at all.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import numpy as np
from scipy import signal

from scenefold import media
from scenefold.audio_offset import parabolic_offset

FPS = 30.0  # working copies are a steady 30 frames a second, starting at clip time 0
GRID = (32, 18)  # each frame is shrunk to this before its brightness is taken
SMOOTH_S = 2.0  # changes slower than this (a pan, the camera's own exposure) are taken out
FAR_S = 5.0  # a match is judged against lags at least this far from it
MIN_FAR_LAGS = 10  # ...and only when there are this many of them to judge against
# How far the match must lead the best lag anywhere else, in standard deviations of the rest.
# Measured: a pattern that merely repeats (video keyframes a second apart) leads by at most 0.50,
# while real matches lead by 0.84-2.71 on drawn clips and 0.60-1.97 on two stadium concerts.
MIN_LEAD = 0.6
# Grey levels a picture must actually move before it is worth matching at all (see match_pictures).
MIN_SIGNAL = 0.05
SOUND_M_PER_S = 343.0


@dataclass(frozen=True)
class PictureMatch:
    lag_s: float  # on A's clock, B's time 0 is this many seconds after A's, by the pictures
    clearness: float  # how many standard deviations the match stands above far-away lags
    overlap_s: float


class PictureSolution(NamedTuple):
    heard_late_s: dict[str, float]  # per clip, counted from the clip nearest the sound
    used: list[bool]  # parallel to the differences given: which ones the answer rests on
    worst_residual_s: float | None  # how far the kept differences sit from one delay per clip


def load_brightness(path: Path) -> np.ndarray:
    """How bright each frame of a working copy is, as a curve at FPS frames a second."""
    width, height = GRID
    means = [
        np.frombuffer(frame, dtype=np.uint8).mean()
        for frame in media.grey_frames(path, width, height)
    ]
    return np.array(means)


def changes(curve: np.ndarray, fps: float = FPS) -> np.ndarray:
    """Brightness with slow drifts taken out and scaled, so two clips can be compared."""
    window = max(3, int(SMOOTH_S * fps) | 1)
    padded = np.pad(curve, window // 2, mode="edge")
    slow = np.convolve(padded, np.ones(window) / window, mode="valid")[: len(curve)]
    quick = curve - slow
    return quick / (quick.std() or 1.0)


def _movement(curve: np.ndarray, fps: float) -> float:
    """How much the picture really moves, in grey levels, once slow drifts are taken out."""
    window = max(3, int(SMOOTH_S * fps) | 1)
    padded = np.pad(curve, window // 2, mode="edge")
    slow = np.convolve(padded, np.ones(window) / window, mode="valid")[: len(curve)]
    return float((curve - slow).std())


def match_pictures(
    a: np.ndarray,
    b: np.ndarray,
    around_s: float,
    *,
    search_s: float = 2.0,
    min_overlap_s: float = 5.0,
    drift_ppm: float = 0.0,
    fps: float = FPS,
) -> PictureMatch | None:
    """Match two brightness curves near `around_s`, where the sound says B's time 0 sits on A's.

    None when the curves cannot be compared: too little overlap, or too few far-away lags to tell a
    real match from the rest. `drift_ppm` is how much faster B's clock ran than A's; B is stretched
    onto A's clock first, exactly as the sound is.
    """
    if drift_ppm:
        b = _stretch(b, drift_ppm)
    # A picture that never changes carries nothing to match. Encoding still leaves a trace of noise
    # on it, `changes` scales that trace up to the size of a real signal, and two traces can then
    # line up by luck. So the raw curves are checked first: a steadily lit room moves 0.00 grey
    # levels here and a few thousandths on other machines, while a lighting show moves about 19.
    if min(_movement(a, fps), _movement(b, fps)) < MIN_SIGNAL:
        return None
    left, right = changes(a, fps), changes(b, fps)
    lags = np.arange(-(len(right) - 1), len(left))
    overlap = np.minimum(len(left), lags + len(right)) - np.maximum(0, lags)
    if not len(lags) or overlap.max() < min_overlap_s * fps:
        return None
    # Dividing by the square root of the overlap, not the overlap itself, puts every lag on the
    # same scale: curves that do not match score about as far from zero however long they share.
    scores = signal.correlate(left, right, mode="full", method="fft") / np.sqrt(
        np.maximum(overlap, 1)
    )
    usable = overlap >= min_overlap_s * fps
    near = usable & (np.abs(lags - round(around_s * fps)) <= round(search_s * fps))
    if not near.any():
        return None
    best = int(np.argmax(np.where(near, scores, -np.inf)))
    far = usable & (np.abs(lags - lags[best]) > FAR_S * fps)
    if far.sum() < MIN_FAR_LAGS or scores[far].std() <= 0:
        return None
    # Anything that repeats matches at every repeat: video encoding leaves a mark on every
    # keyframe, a second apart, and stage lighting follows the beat. So the match also has to lead
    # the best lag anywhere else, not merely be the best where the sound said to look.
    spread = scores[far].std()
    if scores[best] - scores[far].max() <= MIN_LEAD * spread:
        return None
    clearness = (scores[best] - np.median(scores[far])) / spread
    lag = (lags[best] + parabolic_offset(scores, best)) / fps
    return PictureMatch(
        lag_s=float(lag), clearness=float(clearness), overlap_s=float(overlap[best] / fps)
    )


def solve_delays(
    clip_ids: list[str],
    differences: list[tuple[str, str, float, float]],
    *,
    max_residual_s: float = 0.06,
) -> PictureSolution:
    """One delay per clip from pairwise (clip A, clip B, picture minus sound, weight) differences.

    Each difference says `late[b] - late[a]`: how much later than the sound the pictures want B to
    sit. Solving them together gives every clip one number, counted from the clip nearest the
    sound. Differences that disagree with the rest by more than `max_residual_s` are dropped one at
    a time, worst first, as the sound solver does. Clips outside the largest connected set of the
    kept differences get no answer at all.
    """
    kept = list(range(len(differences)))
    while True:
        group = _largest_group(clip_ids, [differences[i] for i in kept])
        inside = [i for i in kept if differences[i][0] in group and differences[i][1] in group]
        if not inside:
            return PictureSolution({}, [False] * len(differences), None)
        values, residuals = _fit(group, [differences[i] for i in inside])
        worst = float(np.max(np.abs(residuals)))
        if worst <= max_residual_s or len(inside) == 1:
            break
        kept.remove(inside[int(np.argmax(np.abs(residuals)))])

    nearest = min(values.values())
    used = [False] * len(differences)
    for i in inside:
        used[i] = True
    return PictureSolution(
        heard_late_s={clip: value - nearest for clip, value in values.items()},
        used=used,
        worst_residual_s=worst,
    )


def metres(late_s: float) -> float:
    """How much further from the sound a phone stood, from how much later it heard it."""
    return late_s * SOUND_M_PER_S


def _stretch(curve: np.ndarray, drift_ppm: float) -> np.ndarray:
    """B's curve on A's clock: a clock running fast fits the same moments into fewer frames."""
    count = max(2, round(len(curve) / (1 + drift_ppm * 1e-6)))
    return np.interp(np.linspace(0, len(curve) - 1, count), np.arange(len(curve)), curve)


def _largest_group(
    clip_ids: list[str], differences: list[tuple[str, str, float, float]]
) -> list[str]:
    """The biggest set of clips joined by differences; ties go to the clips listed first."""
    parent = {clip: clip for clip in clip_ids}

    def root(clip: str) -> str:
        while parent[clip] != clip:
            parent[clip] = parent[parent[clip]]
            clip = parent[clip]
        return clip

    for a, b, _, _ in differences:
        if a in parent and b in parent:
            parent[root(a)] = root(b)
    groups: dict[str, list[str]] = {}
    for clip in clip_ids:
        groups.setdefault(root(clip), []).append(clip)
    return max(groups.values(), key=lambda group: (len(group), -clip_ids.index(group[0])))


def _fit(
    group: list[str], differences: list[tuple[str, str, float, float]]
) -> tuple[dict[str, float], np.ndarray]:
    """Least squares for one delay per clip, with the clips averaging to zero, plus residuals."""
    column = {clip: i for i, clip in enumerate(group)}
    rows = np.zeros((len(differences) + 1, len(group)))
    values = np.zeros(len(differences) + 1)
    weights = np.array([weight for *_, weight in differences] + [1.0])
    for row, (a, b, difference, weight) in enumerate(differences):
        rows[row, column[b]], rows[row, column[a]] = weight, -weight
        values[row] = weight * difference
    rows[-1, :] = 1.0  # the clips average out to zero, so the answer cannot slide as a whole
    solution = np.linalg.lstsq(rows, values, rcond=None)[0]
    residuals = (rows[:-1] @ solution - values[:-1]) / weights[:-1]
    return {clip: float(solution[i]) for clip, i in column.items()}, residuals
