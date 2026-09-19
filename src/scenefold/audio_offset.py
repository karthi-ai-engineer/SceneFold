"""Measure how far apart two recordings of the same event are, from their sound.

Uses GCC-PHAT with soft whitening (beta): the cross-spectrum of the two recordings is flattened so
every frequency counts about equally. That turns the cross-correlation into a sharp peak at the true
lag and keeps loud low rumble from dominating. No speech recognition is needed; any sound that
changes over time (music, claps, cheering, traffic) works.

Phone clocks are not perfect: one phone's audio can run tens, on some models hundreds, of parts per
million (ppm) faster than another's, so the lag slowly changes during a long recording and the
single peak smears out.
After the first match, the overlap is cut into short windows, each window's lag is measured near
the match, and a straight line through those lags gives the drift. The second recording is then
stretched to cancel the drift and matched again, which restores a sharp peak. If strong drift over
a long overlap smeared the first match away entirely, one-minute pieces are matched to find it.
"""

import wave
from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np
from scipy import fft, signal, stats

# Peaks this close to the best one count as the same match (room reflections, filter smear).
EXCLUSION_S = 0.1
# A window's lag may sit this far from the drift line and still count as on it.
LINE_TOLERANCE_S = 0.005
# Windows must be searched a little beyond the drift itself: the first match can be off a bit.
SEARCH_MARGIN_S = 0.05
# Drift is only reported when the windows on the line span at least this long.
MIN_DRIFT_SPAN_S = 20.0
# Stretching is skipped when the drift moves the lag less than this over the whole overlap.
MIN_DRIFT_EFFECT_S = 0.00025
# When the whole-clip match shows no drift line, up to this many pieces of B this long are matched
# instead: strong drift over a long overlap smears a whole-clip match far more than a short piece.
PIECE_S = 60.0
MAX_PIECES = 4
# To check that a match holds through the whole overlap, each window looks for its own best lag
# this far either side, and agrees when it lands within this of the pair's lag (one video frame).
AGREE_SEARCH_S = 1.0
AGREE_TOLERANCE_S = 0.03
AGREE_MAX_WINDOWS = 24  # a long overlap is sampled at this many evenly spaced windows


@dataclass(frozen=True)
class OffsetMeasurement:
    lag_s: float  # on A's clock, B's time 0 is lag_s seconds after A's (negative: B started first)
    confidence: float  # how clearly the best match beats the next-best one; higher is better
    overlap_s: float  # how long both recordings overlap at that lag
    drift_ppm: float | None = None  # how much faster B's clock ran than A's; None: not measurable
    windows: int = 0  # windows the overlap holds
    agreement: float | None = None  # share of windows whose own best lag matches the pair's


def load_audio(path: Path, rate: int) -> np.ndarray:
    """Mono float32 samples of a 16-bit WAV (ingest's proxies/<clip>.wav), resampled to `rate`."""
    with wave.open(str(path), "rb") as wav:
        if wav.getsampwidth() != 2:
            raise ValueError(f"{path}: expected a 16-bit PCM WAV")
        channels, source_rate = wav.getnchannels(), wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1, dtype=np.float32)
    if source_rate != rate:
        common = gcd(rate, source_rate)
        samples = signal.resample_poly(samples, rate // common, source_rate // common)
    return np.asarray(samples, dtype=np.float32)


def measure_offset(
    a: np.ndarray,
    b: np.ndarray,
    rate: int,
    *,
    beta: float = 0.8,
    min_overlap_s: float = 5.0,
    window_s: float = 10.0,
    max_drift_ppm: float = 1000.0,
) -> OffsetMeasurement | None:
    """None when the two recordings can't overlap by at least min_overlap_s at any lag."""
    a, b = _prepare(a), _prepare(b)
    min_overlap = max(1, round(min_overlap_s * rate))
    found = _best_lag(a, b, rate, beta, min_overlap)
    if found is None:
        return None
    lag, confidence = found
    drift = _drift_ppm(a, b, lag, rate, beta, window_s, max_drift_ppm)
    from_pieces = False
    if drift is None and (piece_lag := _lag_from_pieces(a, b, rate, beta)) is not None:
        drift = _drift_ppm(a, b, piece_lag, rate, beta, window_s, max_drift_ppm)
        from_pieces = drift is not None
    small = drift is not None and abs(drift) * 1e-6 * _overlap(lag, len(a), len(b)) < (
        MIN_DRIFT_EFFECT_S * rate
    )
    if drift is None or (small and not from_pieces):
        return _measurement(a, b, lag, confidence, rate, beta, window_s, drift)

    # Put B on A's clock (B ran fast: fewer samples), then match again for a sharp peak.
    stretched = signal.resample(b, round(len(b) / (1 + drift * 1e-6))).astype(np.float32)
    again = _best_lag(a, stretched, rate, beta, min_overlap)
    if again is None or again[1] < 0.9 * confidence:  # the line was not real drift after all
        return _measurement(a, b, lag, confidence, rate, beta, window_s, None)
    return _measurement(a, stretched, again[0], again[1], rate, beta, window_s, drift)


def _best_lag(
    a: np.ndarray, b: np.ndarray, rate: int, beta: float, min_overlap: int
) -> tuple[float, float] | None:
    """(lag in samples, confidence) of the strongest match with at least min_overlap samples."""
    if min(len(a), len(b)) < min_overlap:
        return None
    # lags (in samples) at which the recordings still overlap by at least min_overlap
    lowest, highest = min_overlap - len(b), len(a) - min_overlap
    strength = _strength(a, b, beta, lowest, highest)
    if strength is None:  # silence: nothing to match
        return float(max(lowest, min(0, highest))), 0.0
    best = int(np.argmax(strength))
    peak = float(strength[best])
    exclusion = round(EXCLUSION_S * rate)
    outside = np.concatenate(
        [strength[: max(0, best - exclusion)], strength[best + exclusion + 1 :]]
    )
    runner_up = float(outside.max()) if outside.size else peak
    confidence = peak / runner_up if runner_up > 0 else 0.0
    return lowest + best + _parabolic_offset(strength, best), confidence


def _lag_from_pieces(a: np.ndarray, b: np.ndarray, rate: int, beta: float) -> float | None:
    """Lag (samples) from the best match among evenly spaced pieces of B (both clips long)."""
    size = round(PIECE_S * rate)
    count = min(MAX_PIECES, len(b) // size)
    if count < 2 or len(a) < 2 * size:
        return None
    best = None
    for start in np.linspace(0, len(b) - size, count).round().astype(int):
        found = _best_lag(a, b[start : start + size], rate, beta, size // 2)
        if found is not None and (best is None or found[1] > best[1]):
            best = (found[0] - start, found[1])
    return None if best is None else best[0]


def _strength(
    a: np.ndarray, b: np.ndarray, beta: float, lowest: int, highest: int
) -> np.ndarray | None:
    """|GCC-PHAT-beta| at lags lowest..highest (samples), or None when there is no sound."""
    size = fft.next_fast_len(len(a) + len(b), real=True)
    cross = fft.rfft(a, size, workers=-1)
    cross *= np.conj(fft.rfft(b, size, workers=-1))
    magnitude = np.abs(cross)
    loudest = float(magnitude.max())
    if not np.isfinite(loudest) or loudest <= 0:
        return None
    cross /= (magnitude + loudest * 1e-6) ** beta
    correlation = fft.irfft(cross, size, workers=-1)

    # correlation[k] holds lag k for k >= 0 and lag k - size for negative lags
    if highest < 0:
        window = correlation[size + lowest : size + highest + 1]
    elif lowest >= 0:
        window = correlation[lowest : highest + 1]
    else:
        window = np.concatenate([correlation[size + lowest :], correlation[: highest + 1]])
    return np.abs(window)  # a phone with inverted microphone polarity gives a negative peak


def _drift_ppm(
    a: np.ndarray,
    b: np.ndarray,
    lag: float,
    rate: int,
    beta: float,
    window_s: float,
    max_drift_ppm: float,
) -> float | None:
    """How much faster B's clock runs than A's, from the lag in short windows across the overlap.

    None when there are too few windows, or when most windows don't sit on one straight line
    (unrelated sound, or music too repetitive to follow).
    """
    overlap_s = _overlap(lag, len(a), len(b)) / rate
    search_s = SEARCH_MARGIN_S + max_drift_ppm * 1e-6 * overlap_s
    found = [w for w in _window_lags(a, b, lag, rate, beta, window_s, search_s) if w[1] is not None]
    if len(found) < 3:
        return None

    t, lags_s = np.array([w[0] for w in found]), np.array([w[1] for w in found])
    # Theil-Sen takes the median of the slopes between window pairs, so outliers can't tilt it
    slope, intercept = stats.theilslopes(lags_s, t)[:2]
    on_line = np.abs(lags_s - (intercept + slope * t)) <= LINE_TOLERANCE_S
    if on_line.sum() < 3 or 2 * on_line.sum() <= len(lags_s):
        return None
    if np.ptp(t[on_line]) < MIN_DRIFT_SPAN_S:
        return None
    slope = np.polyfit(t[on_line], lags_s[on_line], 1)[0]
    drift = -float(slope) * 1e6  # B running fast makes the lag shrink over time
    return drift if abs(drift) <= max_drift_ppm else None


def _agreement(
    a: np.ndarray, b: np.ndarray, lag: float, rate: int, beta: float, window_s: float
) -> tuple[int, float | None]:
    """Windows in the overlap, and the share whose own best lag matches the pair's.

    A true match holds through the whole overlap. The same song played on another night matches
    only where its recorded backing track plays, so its windows find other lags more often.
    """
    windows = _window_lags(a, b, lag, rate, beta, window_s, AGREE_SEARCH_S, AGREE_MAX_WINDOWS)
    if not windows:
        return 0, None
    agreeing = sum(
        1
        for _, found in windows
        if found is not None and abs(found - lag / rate) <= AGREE_TOLERANCE_S
    )
    return len(windows), agreeing / len(windows)


def _window_lags(
    a: np.ndarray,
    b: np.ndarray,
    lag: float,
    rate: int,
    beta: float,
    window_s: float,
    search_s: float,
    limit: int | None = None,
) -> list[tuple[float, float | None]]:
    """(centre on A's clock, best lag) in seconds for each window of the overlap.

    Each window of A is matched against B within search_s of the pair's lag (samples); the lag is
    None when there is nothing to compare (silence, or the window runs off B). With a limit, a long
    overlap is sampled at that many evenly spaced windows.
    """
    start, end = max(0.0, lag), min(float(len(a)), lag + len(b))
    size = round(window_s * rate)
    if size < 1:
        return []
    count = int((end - start) // size)
    search = round(search_s * rate)
    margin = ((end - start) - count * size) / 2
    indices = range(count)
    if limit is not None and count > limit:
        indices = np.unique(np.linspace(0, count - 1, limit).round().astype(int))
    found: list[tuple[float, float | None]] = []
    for index in indices:
        first = int(start + margin + index * size)  # window start on A
        low = max(0, round(first - lag) - search)
        high = min(len(b), round(first - lag) + size + search)
        centre = round(lag) + low - first  # expected lag between the window and b[low:high]
        lowest = max(centre - search, size // 2 - (high - low))
        highest = min(centre + search, size - size // 2)
        time = (first + size / 2) / rate
        strength = (
            _strength(a[first : first + size], b[low:high], beta, lowest, highest)
            if lowest <= highest
            else None
        )
        if strength is None:
            found.append((time, None))
            continue
        best = int(np.argmax(strength))
        found.append(
            (time, (lowest + best + _parabolic_offset(strength, best) + first - low) / rate)
        )
    return found


def _prepare(samples: np.ndarray) -> np.ndarray:
    samples = np.nan_to_num(np.asarray(samples, dtype=np.float32).ravel())
    return samples - samples.mean(dtype=np.float64).astype(np.float32) if samples.size else samples


def _parabolic_offset(values: np.ndarray, index: int) -> float:
    """Sub-sample position of a peak from its two neighbours (0 at the edges of the search)."""
    if index == 0 or index == len(values) - 1:
        return 0.0
    left, middle, right = (float(v) for v in values[index - 1 : index + 2])
    curvature = left - 2 * middle + right
    if curvature >= 0:
        return 0.0
    return float(np.clip(0.5 * (left - right) / curvature, -0.5, 0.5))


def _overlap(lag: float, len_a: int, len_b: int) -> float:
    return max(0.0, min(len_a, lag + len_b) - max(0.0, lag))


def _measurement(
    a: np.ndarray,
    b: np.ndarray,
    lag: float,
    confidence: float,
    rate: int,
    beta: float,
    window_s: float,
    drift: float | None,
) -> OffsetMeasurement:
    """The result for B (already on A's clock if its drift was cancelled) at this lag."""
    windows, agreement = _agreement(a, b, lag, rate, beta, window_s)
    return OffsetMeasurement(
        lag_s=lag / rate,
        confidence=confidence,
        overlap_s=_overlap(lag, len(a), len(b)) / rate,
        drift_ppm=drift,
        windows=windows,
        agreement=agreement,
    )
