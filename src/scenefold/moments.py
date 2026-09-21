"""Moments in a clip that can be timed exactly: a sudden sound, or the picture changing.

A model watching a ten-second window says "the lights change and the crowd cheers", but not *when*
inside those ten seconds. Its own times are only as fine as the window it was shown. Onsets are
the opposite: a clap, a drum hit, a flash of light can be placed to a few hundredths of a second
without understanding anything at all.

So the two are put together. The moments here are found by arithmetic, and what the models said is
pulled onto the nearest one. The model still says what happened; the moment says when.

Both kinds are measured against the clip they come from, not against some fixed idea of loud or
bright: strength is what stands out in *this* recording.
"""

from pathlib import Path

import numpy as np
from scipy import signal

from scenefold import audio_offset, picture_offset
from scenefold.observations import Moment

SOUND, PICTURE = "sound", "picture"
RATE = 8000  # sound is read at this rate: onsets need timing, not fidelity
HOP_S = 0.01  # how finely the sound is looked at
MIN_GAP_S = 0.15  # two peaks closer than this are one moment
STANDS_OUT = 4.0  # how far above the usual a peak must rise to count, in robust deviations
MOST = 400  # at most this many moments a clip, keeping the strongest


def sound_moments(wav: Path, rate: int = RATE) -> list[Moment]:
    """When the sound suddenly grows: claps, hits, a cheer starting, a word beginning."""
    samples = audio_offset.load_audio(wav, rate)
    if len(samples) < rate // 10:
        return []
    hop = max(1, int(HOP_S * rate))
    window = hop * 4
    _, times, spectrum = signal.stft(samples, fs=rate, nperseg=window, noverlap=window - hop)
    loudness = np.abs(spectrum)
    # Growth only: sound dying away is not an event, and this is what makes a clap stand out from
    # steady noise of the same volume.
    flux = np.maximum(0.0, np.diff(loudness, axis=1)).sum(axis=0)
    return _peaks(flux, times[1:], SOUND)


def picture_moments(video: Path) -> list[Moment]:
    """When the picture suddenly changes: a flash, a light cue, a cut in an edited upload."""
    brightness = picture_offset.load_brightness(video)
    if len(brightness) < 4:
        return []
    change = np.abs(np.diff(picture_offset.changes(brightness)))
    times = np.arange(1, len(brightness)) / picture_offset.FPS
    return _peaks(change, times, PICTURE)


def snap(t_s: float, moments: list[Moment], within_s: float = 0.4) -> Moment | None:
    """The strongest moment near a time, or None when nothing happened near it.

    Near is generous on purpose: a model's times are coarse, and half a second either way is
    usually the difference between a description and the frame it belongs to.
    """
    close = [m for m in moments if abs(m.t_s - t_s) <= within_s]
    return max(close, key=lambda m: m.strength) if close else None


def strongest(moments: list[Moment], t_start_s: float, t_end_s: float) -> Moment | None:
    """The moment that stands out most inside a stretch of the clip."""
    inside = [m for m in moments if t_start_s <= m.t_s < t_end_s]
    return max(inside, key=lambda m: m.strength) if inside else None


def _peaks(values: np.ndarray, times: np.ndarray, kind: str) -> list[Moment]:
    """Peaks that stand out from the usual run of the signal, strongest first."""
    if not len(values) or not np.any(values > 0):
        return []
    middle = float(np.median(values))
    # median absolute deviation, which a few loud events cannot inflate the way a standard
    # deviation can: one firework should not raise the bar for everything else
    spread = float(np.median(np.abs(values - middle))) * 1.4826
    if spread <= 0:
        spread = float(values.std()) or 1.0
    found, _ = signal.find_peaks(
        values,
        height=middle + STANDS_OUT * spread,
        distance=max(1, int(MIN_GAP_S / (times[1] - times[0]))) if len(times) > 1 else 1,
    )
    if not len(found):
        return []
    heights = values[found]
    strongest_height = float(heights.max()) or 1.0
    moments = [
        Moment(
            t_s=round(float(times[i]), 3), kind=kind, strength=round(float(h / strongest_height), 4)
        )
        for i, h in zip(found, heights, strict=True)
    ]
    moments.sort(key=lambda m: -m.strength)
    return sorted(moments[:MOST], key=lambda m: m.t_s)
