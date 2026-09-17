"""Synthetic event audio for sync tests: one scene, heard by several fake phones.

A phone recording is a window of the scene, with its own volume, microphone noise, and room echo.
The true offset of each window is known, so measured offsets can be checked in milliseconds.
"""

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

RATE = 48_000


def scene(seconds: float, seed: int = 0, rate: int = RATE) -> np.ndarray:
    """Crowd-like noise, speech-like bursts, and claps: sound that changes over time."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    crowd = np.cumsum(rng.standard_normal(n)) * 0.002  # reddish noise
    crowd -= np.convolve(crowd, np.ones(rate // 50) / (rate // 50), mode="same")  # remove drift
    envelope = np.repeat(rng.uniform(0.1, 1.0, int(seconds * 8) + 1), rate // 8)[:n]
    bursts = rng.standard_normal(n) * 0.3 * envelope
    claps = np.zeros(n)
    for start in rng.integers(0, n - rate // 20, size=int(seconds)):
        claps[start : start + rate // 50] += rng.standard_normal(rate // 50) * 0.8
    signal = crowd + bursts + claps
    return (signal / np.max(np.abs(signal)) * 0.5).astype(np.float32)


@dataclass(frozen=True)
class Phone:
    start_s: float  # where this recording starts in scene time (the true offset)
    seconds: float
    gain: float = 1.0
    snr_db: float = 30.0  # microphone noise level
    echo: float = 0.0  # 0 = dry room; 0.5 = strong reflections
    seed: int = 1


def record(signal: np.ndarray, phone: Phone, rate: int = RATE) -> np.ndarray:
    """What one phone hears: a window of the scene with its own gain, noise, and echo."""
    rng = np.random.default_rng(phone.seed)
    start = int(round(phone.start_s * rate))
    clip = signal[start : start + int(phone.seconds * rate)].astype(np.float64) * phone.gain
    if phone.echo:
        impulse = np.zeros(int(0.08 * rate))
        impulse[0] = 1.0
        for delay_ms in (11, 23, 37, 58, 71):
            impulse[int(delay_ms * rate / 1000)] = phone.echo * rng.uniform(0.3, 1.0)
        clip = np.convolve(clip, impulse)[: len(clip)]
    noise_power = np.mean(clip**2) / 10 ** (phone.snr_db / 10)
    clip += rng.standard_normal(len(clip)) * np.sqrt(noise_power)
    return np.clip(clip, -1.0, 1.0).astype(np.float32)


def write_wav(path: Path, samples: np.ndarray, rate: int = RATE) -> Path:
    """Mono 16-bit WAV, like ingest's working audio."""
    data = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(data)
    return path
