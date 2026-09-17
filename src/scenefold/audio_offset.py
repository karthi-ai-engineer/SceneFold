"""Measure how far apart two recordings of the same event are, from their sound.

Uses GCC-PHAT with soft whitening (beta): the cross-spectrum of the two recordings is flattened so
every frequency counts about equally. That turns the cross-correlation into a sharp peak at the true
lag and keeps loud low rumble from dominating. No speech recognition is needed; any sound that
changes over time (music, claps, cheering, traffic) works.
"""

import wave
from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np
from scipy import fft, signal

# Peaks this close to the best one count as the same match (room reflections, filter smear).
EXCLUSION_S = 0.1


@dataclass(frozen=True)
class OffsetMeasurement:
    lag_s: float  # B's time 0 is lag_s seconds after A's (negative: B started first)
    confidence: float  # how clearly the best match beats the next-best one; higher is better
    overlap_s: float  # how long both recordings overlap at that lag


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
    a: np.ndarray, b: np.ndarray, rate: int, *, beta: float = 0.8, min_overlap_s: float = 5.0
) -> OffsetMeasurement | None:
    """None when the two recordings can't overlap by at least min_overlap_s at any lag."""
    a, b = _prepare(a), _prepare(b)
    min_overlap = max(1, round(min_overlap_s * rate))
    if min(len(a), len(b)) < min_overlap:
        return None
    # lags (in samples) at which the recordings still overlap by at least min_overlap
    lowest, highest = min_overlap - len(b), len(a) - min_overlap

    size = fft.next_fast_len(len(a) + len(b), real=True)
    cross = fft.rfft(a, size, workers=-1)
    cross *= np.conj(fft.rfft(b, size, workers=-1))
    magnitude = np.abs(cross)
    loudest = float(magnitude.max())
    if not np.isfinite(loudest) or loudest <= 0:  # silence: nothing to match
        return _measurement(max(lowest, min(0, highest)), 0.0, len(a), len(b), rate)
    cross /= (magnitude + loudest * 1e-6) ** beta
    correlation = fft.irfft(cross, size, workers=-1)

    # correlation[k] holds lag k for k >= 0 and lag k - size for negative lags
    if highest < 0:
        window = correlation[size + lowest : size + highest + 1]
    elif lowest >= 0:
        window = correlation[lowest : highest + 1]
    else:
        window = np.concatenate([correlation[size + lowest :], correlation[: highest + 1]])
    strength = np.abs(window)  # a phone with inverted microphone polarity gives a negative peak

    best = int(np.argmax(strength))
    peak = float(strength[best])
    exclusion = round(EXCLUSION_S * rate)
    outside = np.concatenate(
        [strength[: max(0, best - exclusion)], strength[best + exclusion + 1 :]]
    )
    runner_up = float(outside.max()) if outside.size else peak
    confidence = peak / runner_up if runner_up > 0 else 0.0

    lag = lowest + best + _parabolic_offset(strength, best)
    return _measurement(lag, confidence, len(a), len(b), rate)


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


def _measurement(
    lag: float, confidence: float, len_a: int, len_b: int, rate: int
) -> OffsetMeasurement:
    overlap = min(len_a, lag + len_b) - max(0.0, lag)
    return OffsetMeasurement(
        lag_s=lag / rate, confidence=confidence, overlap_s=max(0.0, overlap) / rate
    )
