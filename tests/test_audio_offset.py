"""Tests for measuring the offset between two recordings (numpy only, no FFmpeg)."""

import time
import wave

import numpy as np
import pytest
from scipy import signal
from synth import RATE, Phone, record, scene, write_wav

from scenefold.audio_offset import EXCLUSION_S, load_audio, measure_offset
from scenefold.timeline import SyncSettings

ANALYSIS_RATE = 8000
THRESHOLD = SyncSettings().min_confidence


def analysis(samples: np.ndarray) -> np.ndarray:
    """48 kHz → 8 kHz, as load_audio does."""
    return signal.resample_poly(samples, 1, RATE // ANALYSIS_RATE).astype(np.float32)


def pair(event: np.ndarray, a: Phone, b: Phone):
    return analysis(record(event, a)), analysis(record(event, b))


@pytest.fixture(scope="module")
def event() -> np.ndarray:
    return scene(70, seed=7)


# --- accuracy


@pytest.mark.parametrize(
    ("start_a", "start_b"),
    [(0.0, 7.30007), (12.2, 3.04321), (5.0, 5.0), (1.0, 1.5171)],  # lags that fall between samples
)
def test_clean_recordings_are_accurate_to_a_millisecond(event, start_a, start_b):
    a, b = pair(event, Phone(start_a, 30, seed=1), Phone(start_b, 25, seed=2))
    measured = measure_offset(a, b, ANALYSIS_RATE)
    assert measured.lag_s == pytest.approx(start_b - start_a, abs=0.001)
    assert measured.confidence > THRESHOLD


@pytest.mark.parametrize(
    ("phone_a", "phone_b"),
    [
        (Phone(0.0, 30, 1.0, snr_db=15, echo=0.4, seed=3), Phone(9.1234, 30, 0.2, 12, 0.4, seed=4)),
        (Phone(14.5, 30, 0.3, snr_db=10, echo=0.4, seed=5), Phone(2.25, 40, 1.0, 15, 0.4, seed=6)),
        (Phone(3.0, 25, 0.5, snr_db=10, echo=0.5, seed=7), Phone(3.0101, 25, 0.5, 10, 0.5, seed=8)),
    ],
)  # fmt: skip
def test_noisy_echoey_phones_are_accurate_to_five_milliseconds(event, phone_a, phone_b):
    a, b = pair(event, phone_a, phone_b)
    measured = measure_offset(a, b, ANALYSIS_RATE)
    error_ms = abs(measured.lag_s - (phone_b.start_s - phone_a.start_s)) * 1000
    print(f"error {error_ms:.3f} ms, confidence {measured.confidence:.1f}")
    assert error_ms < 5
    assert measured.confidence > THRESHOLD


def test_short_clip_inside_a_long_one(event):
    a, b = pair(event, Phone(0.0, 60, seed=1), Phone(31.4159, 8, snr_db=15, seed=2))
    measured = measure_offset(a, b, ANALYSIS_RATE)
    assert measured.lag_s == pytest.approx(31.4159, abs=0.002)
    assert measured.overlap_s == pytest.approx(8.0, abs=0.01)
    # and the same pair the other way round
    reverse = measure_offset(b, a, ANALYSIS_RATE)
    assert reverse.lag_s == pytest.approx(-31.4159, abs=0.002)


def test_overlap_is_reported(event):
    a, b = pair(event, Phone(0.0, 30, seed=1), Phone(20.0, 30, seed=2))
    measured = measure_offset(a, b, ANALYSIS_RATE)
    assert measured.lag_s == pytest.approx(20.0, abs=0.001)
    assert measured.overlap_s == pytest.approx(10.0, abs=0.01)


def test_inverted_microphone_polarity_still_matches(event):
    a, b = pair(event, Phone(0.0, 30, seed=1), Phone(4.321, 20, seed=2))
    measured = measure_offset(a, -b, ANALYSIS_RATE)
    assert measured.lag_s == pytest.approx(4.321, abs=0.001)
    assert measured.confidence > THRESHOLD


# --- telling matches from non-matches


@pytest.mark.parametrize("seed", [11, 12, 13, 14])
def test_unrelated_recordings_have_low_confidence(event, seed):
    other = scene(40, seed=seed)
    a = analysis(record(event, Phone(5.0, 30, seed=1)))
    b = analysis(record(other, Phone(2.0, 30, seed=2)))
    measured = measure_offset(a, b, ANALYSIS_RATE)
    assert measured.confidence < THRESHOLD


def test_no_lag_below_the_minimum_overlap(event):
    # the true overlap is only 3 s; the measurement may not claim a shorter overlap than allowed
    a, b = pair(event, Phone(0.0, 20, seed=1), Phone(17.0, 20, seed=2))
    measured = measure_offset(a, b, ANALYSIS_RATE, min_overlap_s=5.0)
    assert measured.overlap_s >= 5.0 - 1 / ANALYSIS_RATE
    assert measured.lag_s <= 15.0 + 1 / ANALYSIS_RATE


def test_too_short_to_overlap_returns_none(event):
    a, b = pair(event, Phone(0.0, 30, seed=1), Phone(1.0, 4, seed=2))
    assert measure_offset(a, b, ANALYSIS_RATE, min_overlap_s=5.0) is None
    assert measure_offset(np.zeros(0, np.float32), a, ANALYSIS_RATE) is None


def test_silence_and_constant_input_do_not_crash(event):
    a = analysis(record(event, Phone(0.0, 20, seed=1)))
    silent = np.zeros_like(a)
    constant = np.full_like(a, 0.25)
    for other in (silent, constant):
        measured = measure_offset(a, other, ANALYSIS_RATE)
        assert measured.confidence < THRESHOLD
        assert np.isfinite(measured.lag_s)


def test_nan_samples_are_ignored(event):
    a, b = pair(event, Phone(0.0, 30, seed=1), Phone(6.5, 20, seed=2))
    b[1000:1100] = np.nan
    measured = measure_offset(a, b, ANALYSIS_RATE)
    assert measured.lag_s == pytest.approx(6.5, abs=0.001)


def test_exclusion_zone_covers_room_reflections():
    assert EXCLUSION_S >= 0.08  # synth echoes reach 71 ms; real rooms similar


# --- loading audio


def test_load_audio_resamples_a_48k_wav(tmp_path):
    seconds = 3
    t = np.arange(seconds * RATE) / RATE
    tone = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    path = write_wav(tmp_path / "tone.wav", tone)

    loaded = load_audio(path, ANALYSIS_RATE)
    assert loaded.dtype == np.float32
    assert len(loaded) == seconds * ANALYSIS_RATE
    spectrum = np.abs(np.fft.rfft(loaded))
    assert np.argmax(spectrum) * ANALYSIS_RATE / len(loaded) == pytest.approx(440, abs=1)
    assert np.max(np.abs(loaded[100:-100])) == pytest.approx(0.5, abs=0.02)


def test_load_audio_keeps_rate_and_mixes_stereo(tmp_path):
    left = np.full(1000, 16000, dtype="<i2")
    right = np.full(1000, -8000, dtype="<i2")
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(ANALYSIS_RATE)
        wav.writeframes(np.column_stack([left, right]).tobytes())
    loaded = load_audio(path, ANALYSIS_RATE)
    assert len(loaded) == 1000
    assert loaded == pytest.approx(np.full(1000, 4000 / 32768), abs=1e-6)


def test_load_audio_rejects_other_sample_formats(tmp_path):
    path = tmp_path / "eight_bit.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(ANALYSIS_RATE)
        wav.writeframes(bytes(100))
    with pytest.raises(ValueError, match="16-bit"):
        load_audio(path, ANALYSIS_RATE)


# --- scale


def test_five_minute_recordings_are_quick():
    event = scene(330, seed=3, rate=ANALYSIS_RATE)
    a = record(event, Phone(0.0, 300, snr_db=15, seed=1), rate=ANALYSIS_RATE)
    b = record(event, Phone(27.125, 300, snr_db=15, seed=2), rate=ANALYSIS_RATE)
    started = time.perf_counter()
    measured = measure_offset(a, b, ANALYSIS_RATE)
    elapsed = time.perf_counter() - started
    print(f"5-minute pair measured in {elapsed:.2f} s")
    assert measured.lag_s == pytest.approx(27.125, abs=0.001)
    assert elapsed < 30
