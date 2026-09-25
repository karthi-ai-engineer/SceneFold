"""Find single sounds that several clips heard, and time each arrival on the shared clock.

A clap, a hit, a shout: a sound with a definite instant. Every phone near enough hears it, each a
little later than the one standing closest to it, because sound needs 2.9 ms to cover a metre.
Those small differences are what a camera map is made of (`positions.py`).

The work is in three steps. Each clip's own sound is searched for sharp rises in loudness, which is
what a transient is. Those moments are put on the master clock sync built, so peaks from different
clips can be compared at all, and peaks that land close together are taken to be one sound. Then
each clip's arrival is measured properly: a short piece of audio around the peak is matched against
the same piece from the clip that heard it most clearly, the same GCC-PHAT way sync matches whole
clips. Peak-picking alone is worth a few milliseconds; matching is worth a fraction of one.

What this deliberately does not do is trust a lone peak. A sound fewer than three clips heard is
dropped, and so is one whose arrivals spread wider than any real event could (0.6 s is 200 m), or
whose match is not clearly better than the next-best alignment. A map built on invented sounds
would be worse than no map.
"""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import fft, signal

from scenefold.audio_offset import load_audio, parabolic_offset
from scenefold.positions import SoundEvent
from scenefold.timeline import ClipPlacement, Timeline

RATE = 16_000  # 16 kHz keeps claps sharp; a sample is 0.06 ms, far finer than a map needs
HOP_S = 0.005  # loudness is taken every 5 ms...
WINDOW_S = 0.020  # ...over 20 ms, about the length of a clap's attack
BACKGROUND_S = 0.25  # a rise counts only against the quarter second before it
MIN_RISE_DB = 6.0  # and must be at least this much louder than that, so silence stays quiet
RISE_SPREAD = 4.0  # ...and this many spreads above the clip's usual rise, whichever is stricter
MIN_PEAK_GAP_S = 0.25  # two peaks closer than this are one sound, not two
CLUSTER_S = 0.6  # peaks within this of each other may be the same sound: 0.6 s of travel is 200 m
MATCH_HALF_S = 0.30  # an arrival is measured on this much audio either side of the peak
MATCH_SEARCH_S = 0.25  # ...matched this far either way, which is wider than peak-picking can err
EXCLUSION_S = 0.02  # matches this close to the best one are the same match (reflections)
MIN_MATCH_CONFIDENCE = 1.5  # how clearly the match must beat the next-best alignment
MAX_REFINE_MOVE_S = 0.15  # a match that moves the arrival further than this found something else
MAX_SPREAD_S = 0.6  # arrivals wider apart than this are not one sound
MIN_CLIPS = 3  # a sound two clips heard says nothing about where anybody stood
PHAT_BETA = 0.8  # whitening strength, as in audio_offset


@dataclass(frozen=True)
class Peak:
    """A sharp rise in one clip's loudness."""

    clip_id: str
    t_local_s: float
    t_master_s: float
    rise_db: float


def find_sound_events(
    event_dir: Path,
    timeline: Timeline,
    *,
    rate: int = RATE,
    max_events: int = 60,
) -> list[SoundEvent]:
    """Sounds that several placed clips heard, with the master time each clip heard them.

    Arrivals are in sync's own master time (sound alignment), so their differences are travel-time
    differences and nothing else. The strongest sounds come first, chosen from across the timeline
    rather than from one loud minute.
    """
    audio = _clip_audio(event_dir, timeline, rate)
    if len(audio) < MIN_CLIPS:
        return []

    peaks: list[Peak] = []
    for clip_id, (samples, placement) in audio.items():
        for t_local, rise in zip(*onsets(samples, rate), strict=True):
            peaks.append(Peak(clip_id, t_local, to_master(t_local, placement), rise))

    events = []
    for cluster in _clusters(peaks):
        event = _measure(cluster, audio, rate)
        if event is not None:
            events.append(event)
    return _spread_over_time(events, max_events)


def onsets(samples: np.ndarray, rate: int) -> tuple[np.ndarray, np.ndarray]:
    """When this clip's sound rises sharply, and by how many decibels.

    Loudness is measured in short steps and compared with the quarter second before it, so a clap
    stands out however loud the room already was. Slow swells do not count: a map needs an instant.
    """
    hop, window = max(1, round(HOP_S * rate)), max(2, round(WINDOW_S * rate))
    if len(samples) < window + hop:
        return np.array([]), np.array([])
    squared = np.concatenate([[0.0], np.cumsum(np.asarray(samples, dtype=np.float64) ** 2)])
    starts = np.arange(0, len(samples) - window, hop)
    energy = (squared[starts + window] - squared[starts]) / window
    loudness = 10 * np.log10(energy + 1e-12)

    back = max(1, round(BACKGROUND_S / HOP_S))
    totals = np.concatenate([[0.0], np.cumsum(loudness)])
    index = np.arange(len(loudness))
    first = np.maximum(0, index - back)
    before = (totals[index] - totals[first]) / np.maximum(1, index - first)
    rise = loudness - before

    spread = 1.4826 * float(np.median(np.abs(rise - np.median(rise))))  # robust standard deviation
    threshold = max(MIN_RISE_DB, float(np.median(rise)) + RISE_SPREAD * spread)
    found, _ = signal.find_peaks(
        rise, height=threshold, distance=max(1, round(MIN_PEAK_GAP_S / HOP_S))
    )
    times = (starts[found] + window / 2) / rate
    return times, rise[found]


def to_master(t_local: float, clip: ClipPlacement) -> float:
    """The clip's own time, on the shared clock (timeline.py)."""
    return (clip.offset_s or 0.0) + t_local / (1 + (clip.drift_ppm or 0.0) * 1e-6)


def to_local(t_master: float, clip: ClipPlacement) -> float:
    return (t_master - (clip.offset_s or 0.0)) * (1 + (clip.drift_ppm or 0.0) * 1e-6)


def _clip_audio(
    event_dir: Path, timeline: Timeline, rate: int
) -> dict[str, tuple[np.ndarray, ClipPlacement]]:
    """Every placed clip's sound, at the analysis rate, keyed by clip id."""
    audio: dict[str, tuple[np.ndarray, ClipPlacement]] = {}
    for clip in timeline.clips:
        path = event_dir / "proxies" / f"{clip.clip_id}.wav"
        if not clip.placed or clip.offset_s is None or not path.is_file():
            continue
        try:
            samples = load_audio(path, rate)
        except (OSError, ValueError):
            continue  # a clip whose sound cannot be read simply takes no part
        if samples.size:
            audio[clip.clip_id] = (samples, clip)
    return audio


def _clusters(peaks: list[Peak]) -> list[list[Peak]]:
    """Group peaks from different clips that are close enough in master time to be one sound.

    The loudest unused peak starts a group and each other clip adds its nearest peak. A clip whose
    two nearest peaks are both close and similarly loud is left out of that group: which of them is
    the same sound cannot be told, and a guess would move a camera metres.
    """
    remaining = sorted(peaks, key=lambda p: (-p.rise_db, p.clip_id, p.t_master_s))
    used: set[int] = set()
    by_clip: dict[str, list[Peak]] = {}
    for peak in peaks:
        by_clip.setdefault(peak.clip_id, []).append(peak)
    for clip_peaks in by_clip.values():
        clip_peaks.sort(key=lambda p: p.t_master_s)

    clusters = []
    for seed in remaining:
        if id(seed) in used:
            continue
        group = [seed]
        for clip_id, clip_peaks in by_clip.items():
            if clip_id == seed.clip_id:
                continue
            near = [
                p
                for p in clip_peaks
                if id(p) not in used and abs(p.t_master_s - seed.t_master_s) <= CLUSTER_S
            ]
            if not near:
                continue
            near.sort(key=lambda p: abs(p.t_master_s - seed.t_master_s))
            if len(near) > 1 and near[1].rise_db >= 0.7 * near[0].rise_db:
                continue  # two candidates, no way to tell which: leave this clip out
            group.append(near[0])
        if len(group) >= MIN_CLIPS:
            used.update(id(p) for p in group)
            clusters.append(group)
    return clusters


def _measure(
    cluster: list[Peak], audio: dict[str, tuple[np.ndarray, ClipPlacement]], rate: int
) -> SoundEvent | None:
    """Time each clip's arrival by matching the audio around its peak against the clearest clip."""
    reference = max(cluster, key=lambda p: p.rise_db)
    ref_window = _window(audio[reference.clip_id][0], reference.t_local_s, rate)
    if ref_window is None:
        return None
    ref_start_local, ref_samples = ref_window
    ref_start_master = to_master(ref_start_local, audio[reference.clip_id][1])
    ref_arrival = reference.t_master_s

    arrivals = {reference.clip_id: ref_arrival}
    confidences = []
    for peak in cluster:
        if peak.clip_id == reference.clip_id:
            continue
        window = _window(audio[peak.clip_id][0], peak.t_local_s, rate)
        if window is None:
            continue
        start_local, samples = window
        placement = audio[peak.clip_id][1]
        match = _match(samples, ref_samples, rate)
        if match is None or match[1] < MIN_MATCH_CONFIDENCE:
            continue
        lag_s, confidence = match[0] / rate, match[1]
        start_master = to_master(start_local, placement)
        arrival = (
            ref_arrival
            + (start_master - ref_start_master)
            + lag_s / (1 + (placement.drift_ppm or 0.0) * 1e-6)
        )
        if abs(arrival - peak.t_master_s) > MAX_REFINE_MOVE_S:
            continue  # the match found a different sound, not this peak
        arrivals[peak.clip_id] = arrival
        confidences.append(confidence)

    if len(arrivals) < MIN_CLIPS or not confidences:
        return None
    spread = max(arrivals.values()) - min(arrivals.values())
    if spread > MAX_SPREAD_S:
        return None  # too far apart to be one sound
    return SoundEvent(
        label="transient",
        arrivals_s={clip: round(value, 6) for clip, value in sorted(arrivals.items())},
        confidence=round(min(confidences), 3),  # an event is only as good as its weakest arrival
        spread_s=round(spread, 6),
    )


def _window(samples: np.ndarray, centre_s: float, rate: int) -> tuple[float, np.ndarray] | None:
    """The audio around a peak, and where that piece starts on the clip's own clock."""
    half = round(MATCH_HALF_S * rate)
    middle = round(centre_s * rate)
    first, last = middle - half, middle + half
    if first < 0 or last > len(samples):
        return None  # the sound sits too close to an end to measure it properly
    return first / rate, samples[first:last]


def _match(a: np.ndarray, b: np.ndarray, rate: int) -> tuple[float, float] | None:
    """Where a's sound sits relative to b's, in samples, and how clearly (GCC-PHAT-beta).

    Positive means a's copy of the sound comes later inside its window than b's does.
    """
    a, b = _prepare(a), _prepare(b)
    size = fft.next_fast_len(len(a) + len(b), real=True)
    cross = fft.rfft(a, size, workers=-1)
    cross *= np.conj(fft.rfft(b, size, workers=-1))
    magnitude = np.abs(cross)
    loudest = float(magnitude.max())
    if not np.isfinite(loudest) or loudest <= 0:
        return None  # silence: nothing to match
    cross /= (magnitude + loudest * 1e-6) ** PHAT_BETA
    correlation = fft.irfft(cross, size, workers=-1)

    search = round(MATCH_SEARCH_S * rate)
    # correlation[k] holds lag k for k >= 0 and lag k - size for negative lags; the lag here is
    # (position in a) - (position in b), so a positive lag means a heard it later.
    strength = np.abs(np.concatenate([correlation[size - search :], correlation[: search + 1]]))
    best = int(np.argmax(strength))
    peak = float(strength[best])
    exclusion = round(EXCLUSION_S * rate)
    outside = np.concatenate(
        [strength[: max(0, best - exclusion)], strength[best + exclusion + 1 :]]
    )
    runner_up = float(outside.max()) if outside.size else peak
    if runner_up <= 0:
        return None
    lag = best - search + parabolic_offset(strength, best)
    return lag, peak / runner_up


def _prepare(samples: np.ndarray) -> np.ndarray:
    samples = np.nan_to_num(np.asarray(samples, dtype=np.float32).ravel())
    return samples - samples.mean(dtype=np.float64).astype(np.float32) if samples.size else samples


def _spread_over_time(events: list[SoundEvent], max_events: int) -> list[SoundEvent]:
    """The most confident events, taken from across the timeline rather than from one minute."""
    if len(events) <= max_events:
        return sorted(events, key=lambda e: (-e.confidence, min(e.arrivals_s.values())))
    earliest = min(min(e.arrivals_s.values()) for e in events)
    latest = max(min(e.arrivals_s.values()) for e in events)
    span = max(1e-6, latest - earliest)
    buckets: dict[int, list[SoundEvent]] = {}
    for event in events:
        share = (min(event.arrivals_s.values()) - earliest) / span
        buckets.setdefault(min(max_events - 1, int(share * max_events)), []).append(event)
    chosen: list[SoundEvent] = []
    round_number = 0
    while len(chosen) < max_events and any(buckets.values()):
        for key in sorted(buckets):
            pool = buckets[key]
            if len(pool) > round_number and len(chosen) < max_events:
                chosen.append(sorted(pool, key=lambda e: -e.confidence)[round_number])
        round_number += 1
    return sorted(chosen, key=lambda e: (-e.confidence, min(e.arrivals_s.values())))
