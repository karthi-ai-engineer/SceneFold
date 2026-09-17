"""Unit tests for probe parsing and small helpers (no FFmpeg needed)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from scenefold.ingest import _safe_suffix
from scenefold.manifest import ProxySettings, VideoStream, normalize_event_id
from scenefold.media import ProxyResult, _read_audio_levels, error_summary, parse_probe, proxy_size


def video_stream(**overrides) -> dict:
    stream = {
        "index": 0,
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "r_frame_rate": "30/1",
        "avg_frame_rate": "30/1",
        "pix_fmt": "yuv420p",
        "disposition": {"default": 1},
    }
    return stream | overrides


def audio_stream(**overrides) -> dict:
    stream = {
        "index": 1,
        "codec_type": "audio",
        "codec_name": "aac",
        "sample_rate": "48000",
        "channels": 2,
        "disposition": {"default": 1},
    }
    return stream | overrides


def probe(*streams: dict, **fmt) -> dict:
    return {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "12.5"} | fmt,
        "streams": list(streams),
    }


# --- rotation


@pytest.mark.parametrize(
    ("side_data_rotation", "expected"),
    [(-90, 90), (90, 270), (180, 180), (-180, 180), (0, 0), (-89.6, 90)],
)
def test_rotation_from_display_matrix(side_data_rotation, expected):
    stream = video_stream(
        side_data_list=[{"side_data_type": "Display Matrix", "rotation": side_data_rotation}]
    )
    video = parse_probe(probe(stream)).video
    assert video.rotation == expected
    if expected in (90, 270):
        assert (video.display_width, video.display_height) == (1080, 1920)
    else:
        assert (video.display_width, video.display_height) == (1920, 1080)


def test_rotation_from_legacy_tag():
    video = parse_probe(probe(video_stream(tags={"rotate": "90"}))).video
    assert video.rotation == 90
    assert (video.display_width, video.display_height) == (1080, 1920)


# --- stream facts


def test_frame_rate_and_variable_frame_rate_guess():
    ntsc = parse_probe(probe(video_stream(r_frame_rate="30000/1001", avg_frame_rate="30000/1001")))
    assert ntsc.video.fps == pytest.approx(29.97, abs=0.001)
    assert not ntsc.video.variable_frame_rate

    vfr = parse_probe(probe(video_stream(r_frame_rate="120/1", avg_frame_rate="2997/100")))
    assert vfr.video.variable_frame_rate

    unknown = parse_probe(probe(video_stream(r_frame_rate="0/0", avg_frame_rate="0/0")))
    assert unknown.video.fps is None
    assert not unknown.video.variable_frame_rate


def test_non_square_pixels_give_display_size():
    stream = video_stream(width=720, height=480, sample_aspect_ratio="32:27")
    video = parse_probe(probe(stream)).video
    assert (video.display_width, video.display_height) == (853, 480)


def test_invalid_pixel_aspect_ratio_is_ignored():
    video = parse_probe(probe(video_stream(sample_aspect_ratio="0:1"))).video
    assert (video.display_width, video.display_height) == (1920, 1080)


def test_hdr_and_interlaced_flags():
    hlg = parse_probe(probe(video_stream(color_transfer="arib-std-b67"))).video
    pq = parse_probe(probe(video_stream(color_transfer="smpte2084"))).video
    sdr = parse_probe(probe(video_stream(color_transfer="bt709", field_order="progressive"))).video
    interlaced = parse_probe(probe(video_stream(field_order="tt"))).video
    assert hlg.hdr and pq.hdr and not sdr.hdr
    assert interlaced.interlaced and not sdr.interlaced


def test_cover_art_is_not_a_video_track():
    cover = video_stream(index=1, codec_name="mjpeg", disposition={"attached_pic": 1})
    info = parse_probe(probe(audio_stream(index=0), cover, format_name="mp3"))
    assert info.video is None
    assert info.audio is not None


def test_default_streams_are_preferred():
    first = audio_stream(index=1, disposition={"default": 0})
    second = audio_stream(index=2, disposition={"default": 1})
    info = parse_probe(probe(video_stream(), first, second))
    assert info.audio.index == 2


def test_first_stream_used_when_none_is_default():
    first = audio_stream(index=1, disposition={})
    second = audio_stream(index=2, disposition={})
    assert parse_probe(probe(video_stream(), first, second)).audio.index == 1


@pytest.mark.parametrize(
    ("fmt", "stream_overrides", "is_image"),
    [
        ({"format_name": "image2"}, {"codec_name": "mjpeg"}, True),
        ({"format_name": "png_pipe"}, {"codec_name": "png"}, True),
        ({"format_name": "gif"}, {"codec_name": "gif"}, True),
        ({}, {"codec_name": "hevc", "nb_frames": "1"}, True),
        ({}, {"codec_name": "h264", "nb_frames": "360"}, False),
        ({"format_name": "avi"}, {"codec_name": "mjpeg"}, False),  # motion-JPEG camera video
    ],
)
def test_still_images_are_recognized(fmt, stream_overrides, is_image):
    info = parse_probe(probe(video_stream(**stream_overrides), **fmt))
    assert info.is_image is is_image


def test_audio_only_file():
    info = parse_probe(probe(audio_stream(index=0), format_name="mov,mp4,m4a,3gp,3g2,mj2"))
    assert info.video is None and info.audio is not None


# --- duration and dates


def test_duration_prefers_container_value():
    assert parse_probe(probe(video_stream(duration="9.0"), duration="12.5")).duration_s == 12.5


def test_duration_falls_back_to_matroska_tag():
    stream = video_stream(tags={"DURATION": "00:01:02.500000000"})
    info = parse_probe({"format": {"format_name": "matroska,webm"}, "streams": [stream]})
    assert info.duration_s == 62.5


def test_duration_unknown():
    info = parse_probe({"format": {"format_name": "matroska,webm"}, "streams": [video_stream()]})
    assert info.duration_s is None


def test_recorded_at_prefers_apple_local_time():
    tags = {
        "creation_time": "2026-09-01T10:00:00.000000Z",
        "com.apple.quicktime.creationdate": "2026-09-01T19:00:05+0900",
    }
    recorded = parse_probe(probe(video_stream(), tags=tags)).recorded_at
    assert recorded == datetime(2026, 9, 1, 19, 0, 5, tzinfo=timezone(timedelta(hours=9)))


def test_recorded_at_from_stream_tags_and_naive_times_are_utc():
    stream = video_stream(tags={"creation_time": "2026-09-01 10:00:00"})
    recorded = parse_probe(probe(stream)).recorded_at
    assert recorded == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize("value", ["1904-01-01T00:00:00Z", "1970-01-01T00:00:00Z", "yesterday"])
def test_unset_or_garbage_dates_are_ignored(value):
    assert parse_probe(probe(video_stream(), tags={"creation_time": value})).recorded_at is None


def test_partial_file_warning_marks_incomplete():
    stderr = "[mov,mp4,m4a,3gp,3g2,mj2 @ 000001] stream 0, offset 0x2253: partial file\n"
    assert parse_probe(probe(video_stream()), stderr).incomplete
    assert not parse_probe(probe(video_stream()), "").incomplete


# --- working copy size


def _video(width: int, height: int) -> VideoStream:
    return VideoStream(
        index=0,
        codec="h264",
        width=width,
        height=height,
        display_width=width,
        display_height=height,
    )


@pytest.mark.parametrize(
    ("size", "expected"),
    [
        ((3840, 2160), (1280, 720)),  # 4K landscape
        ((2160, 3840), (720, 1280)),  # 4K portrait
        ((1280, 720), (1280, 720)),  # already small enough
        ((320, 240), (320, 240)),  # never upscaled
        ((321, 241), (322, 242)),  # odd sizes made even
        ((1440, 1080), (960, 720)),  # 4:3
        ((1920, 800), (1728, 720)),  # cinema wide
    ],
)
def test_proxy_size(size, expected):
    width, height = proxy_size(_video(*size), 720)
    assert (width, height) == expected
    assert width % 2 == 0 and height % 2 == 0


# --- FFmpeg output parsing


def test_error_summary_keeps_the_meaningful_lines():
    stderr = (
        "[mov,mp4,m4a,3gp,3g2,mj2 @ 0000019f7f203c00] moov atom not found\n"
        "C:\\clips\\a.mp4: Invalid data found when processing input\n"
        "[vist#0:0/h264 @ 000001] Task finished with error code: -22 (Invalid argument)\n"
    )
    summary = error_summary(stderr)
    assert "moov atom not found" in summary
    assert "Invalid data found" in summary
    assert "Task finished" not in summary
    assert "@ 0000" not in summary


def test_error_summary_falls_back_to_last_line():
    assert error_summary("line one\nsomething odd happened\n") == "something odd happened"
    assert error_summary("") == "unknown FFmpeg error"


def test_audio_levels_use_the_final_report():
    stderr = (
        "[Parsed_volumedetect_2 @ 01] n_samples: 0\n"
        "[Parsed_volumedetect_2 @ 02] n_samples: 288000\n"
        "[Parsed_volumedetect_2 @ 02] mean_volume: -29.7 dB\n"
        "[Parsed_volumedetect_2 @ 02] max_volume: 0.0 dB\n"
        "[Parsed_volumedetect_2 @ 02] histogram_0db: 1440\n"
    )
    result = ProxyResult(width=2, height=2, duration_s=6.0, has_audio=True)
    _read_audio_levels(stderr, result)
    assert result.audio_mean_db == -29.7
    assert result.audio_peak_db == 0.0
    assert result.audio_clipped_fraction == pytest.approx(0.005)


def test_digital_silence_level_is_a_number():
    stderr = "n_samples: 1000\nmean_volume: -inf dB\nmax_volume: -inf dB\n"
    result = ProxyResult(width=2, height=2, duration_s=6.0, has_audio=True)
    _read_audio_levels(stderr, result)
    assert result.audio_peak_db == -120.0
    assert result.audio_clipped_fraction == 0


# --- names and settings


@pytest.mark.parametrize(
    ("name", "expected"),
    [("match-01", "match-01"), ("  Wedding_2026 ", "wedding_2026"), ("a", "a")],
)
def test_event_names_are_normalized(name, expected):
    assert normalize_event_id(name) == expected


@pytest.mark.parametrize(
    "name",
    ["", "  ", "my event", "../escape", "a/b", "-dash-first", "con", "COM1", "x" * 65, "café"],
)
def test_bad_event_names_are_rejected(name):
    with pytest.raises(ValueError):
        normalize_event_id(name)


def test_settings_key_is_stable_and_tracks_changes():
    assert ProxySettings().key() == ProxySettings().key()
    assert ProxySettings().key() != ProxySettings(max_short_side=480).key()


@pytest.mark.parametrize(
    ("name", "suffix"),
    [
        ("IMG_0001.MOV", ".mov"),
        ("clip.mp4", ".mp4"),
        ("video.mp4 (1)", ""),
        ("noext", ""),
        ("weird.m$v", ""),
    ],
)
def test_original_copy_suffix(name, suffix):
    from pathlib import Path

    assert _safe_suffix(Path(name)) == suffix
