"""Synthetic event audio for sync tests: one scene, heard by several fake phones.

A phone recording is a window of the scene, with its own volume, microphone noise, room echo, and
clock drift. The true offset of each window is known, so measured offsets can be checked in
milliseconds.
"""

import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from scipy import ndimage

RATE = 48_000
SOUND_M_PER_S = 343.0


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


def loop(seconds: float, bpm: float = 120, seed: int = 0, rate: int = RATE) -> np.ndarray:
    """Music that repeats exactly every bar: kick, snare, hi-hat, and a four-note bass line."""
    rng = np.random.default_rng(seed)
    beat = round(60 / bpm * rate)
    t = np.arange(beat) / rate
    kick = np.sin(2 * np.pi * 55 * t) * np.exp(-t * 25)
    snare = rng.standard_normal(beat) * np.exp(-t * 30) * 0.6
    hat = rng.standard_normal(beat // 16) * 0.3
    bar = np.zeros(4 * beat)
    for index, note in enumerate((110, 147, 131, 98)):
        part = bar[index * beat : (index + 1) * beat]
        part += kick if index % 2 == 0 else snare
        part += 0.2 * np.sin(2 * np.pi * note * t)
        for at in (0, beat // 2):
            part[at : at + len(hat)] += hat
    count = int(seconds * rate)
    music = np.tile(bar, count // len(bar) + 1)[:count]
    return (music / np.max(np.abs(music)) * 0.5).astype(np.float32)


def show(
    seconds: float,
    night_seed: int,
    backing_level: float = 0.2,
    songs: tuple[tuple[float, float], ...] = ((20, 60), (75, 115)),
    rate: int = RATE,
) -> np.ndarray:
    """One night of a concert: live sound (singing, talk, crowd) that differs every night, plus a
    recorded backing track, identical every night, that plays during the songs."""
    backing = scene(seconds, seed=500, rate=rate) * backing_level
    playing = np.zeros_like(backing)
    for start, end in songs:
        playing[int(start * rate) : int(end * rate)] = 1.0
    return (backing * playing + scene(seconds, seed=night_seed, rate=rate)).astype(np.float32)


@dataclass(frozen=True)
class Phone:
    start_s: float  # where this recording starts in scene time (the true offset)
    seconds: float  # length on the phone's own clock
    gain: float = 1.0
    snr_db: float = 30.0  # microphone noise level
    echo: float = 0.0  # 0 = dry room; 0.5 = strong reflections
    seed: int = 1
    drift_ppm: float = 0.0  # how much faster the phone's clock runs than scene time
    distance_m: float = 0.0  # how far from the sound it stands; its sound arrives 2.9 ms/m late


def record(signal: np.ndarray, phone: Phone, rate: int = RATE) -> np.ndarray:
    """What one phone hears: a window of the scene with its own gain, noise, echo, and clock.

    Phone time t_local shows scene time start_s + t_local / (1 + drift_ppm / 1e6). Sound from
    `distance_m` away arrives that much later, so at phone time 0 it hears an earlier moment than
    it sees: this is why sync, which listens, places a distant phone's picture late.
    """
    rng = np.random.default_rng(phone.seed)
    count = int(phone.seconds * rate)
    start_s = phone.start_s - phone.distance_m / SOUND_M_PER_S
    if phone.drift_ppm:
        positions = start_s * rate + np.arange(count) / (1 + phone.drift_ppm * 1e-6)
        first = max(0, int(positions[0]) - 4)
        piece = signal[first : int(positions[-1]) + 5].astype(np.float64)
        clip = ndimage.map_coordinates(piece, [positions - first], order=3, mode="nearest")
    else:
        start = int(round(start_s * rate))
        clip = signal[start : start + count].astype(np.float64)
    clip *= phone.gain
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


def lighting(seconds: float, seed: int = 0, fps: float = 30.0) -> np.ndarray:
    """Stage lighting as a brightness curve: many pulses at unrelated speeds, never repeating.

    This is what a clip's picture gives sync (see picture_offset.py). Lighting on a regular beat
    would match at every beat, and single-frame flicker does not survive video encoding, so the
    pulses are irregular and none of them is fast.
    """
    rng = np.random.default_rng(seed)
    times = np.arange(int(seconds * fps)) / fps
    speeds = rng.uniform(0.4, 5.0, 14)
    curve = np.zeros_like(times)
    for speed, phase in zip(speeds, rng.uniform(0, 2 * np.pi, 14), strict=True):
        curve += 0.10 / np.sqrt(speed) * np.sin(2 * np.pi * speed * times + phase)
    return 128 + 90 * curve  # around the middle of an 8-bit grey scale, as a real picture is


def filmed(
    curve: np.ndarray,
    phone: Phone,
    fps: float = 30.0,
    noise: float = 0.5,
) -> np.ndarray:
    """The part of a lighting curve one phone recorded, on its own clock, with sensor noise.

    Phone time t_local shows scene time start_s + t_local / (1 + drift_ppm / 1e6), exactly as
    `record` does for sound.
    """
    rng = np.random.default_rng(phone.seed + 5)
    count = int(phone.seconds * fps)
    times = phone.start_s + np.arange(count) / (fps * (1 + phone.drift_ppm * 1e-6))
    seen = np.interp(times, np.arange(len(curve)) / fps, curve)
    return seen + rng.standard_normal(count) * noise
