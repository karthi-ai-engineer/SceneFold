"""End-to-end ingest tests on generated videos (need FFmpeg)."""

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from conftest import (
    click_time,
    decode_audio,
    ffprobe_json,
    flash_time,
    needs_ffmpeg,
    stream,
    wav_info,
)

from scenefold import media
from scenefold.cli import main
from scenefold.ingest import LOCK_NAME, IngestError, Outcome, ingest
from scenefold.manifest import (
    MANIFEST_NAME,
    SCHEMA_VERSION,
    ClipStatus,
    ProxySettings,
    load_manifest,
)

pytestmark = needs_ffmpeg

TIMED_CLIPS = [
    "phone.mp4",
    "audio_late.mp4",
    "video_late.mp4",
    "clip.mkv",
    "recorder.webm",
    "portrait.mov",
    "日本語 動画.mp4",
    "-dash-name.mp4",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy_sources(sources: Path, dest: Path, *names: str) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    for name in names:
        shutil.copy2(sources / name, dest / name)
    return dest


def outcomes(report) -> dict[str, Outcome]:
    return {result.path.name: result.outcome for result in report.results}


# --- picture and sound


@pytest.mark.parametrize("name", TIMED_CLIPS)
def test_picture_and_sound_stay_aligned(batch, tmp_path, name):
    clip = batch.clip(name)
    assert batch.result(name).outcome is Outcome.ADDED
    assert clip.status is ClipStatus.OK, clip.issues
    video = batch.event_dir / clip.proxy.video
    wav = batch.event_dir / clip.proxy.audio

    # the flash frame and the click both sit at 1.000 s of clip time
    assert flash_time(video) == pytest.approx(1.0, abs=0.001)
    assert click_time(wav) == pytest.approx(1.0, abs=0.005)
    assert click_time(decode_audio(video, tmp_path / "proxy_audio.wav")) == pytest.approx(
        1.0, abs=0.01
    )
    # both tracks of the working copy start at time 0
    assert float(stream(video, "video")["start_time"]) == pytest.approx(0, abs=0.001)
    assert float(stream(video, "audio")["start_time"]) == pytest.approx(0, abs=0.001)


def test_working_copy_format(batch):
    clip = batch.clip("phone.mp4")
    video_path = batch.event_dir / clip.proxy.video
    video, audio = stream(video_path, "video"), stream(video_path, "audio")
    assert (video["codec_name"], video["pix_fmt"]) == ("h264", "yuv420p")
    assert video["r_frame_rate"] == video["avg_frame_rate"] == "30/1"
    assert (audio["codec_name"], audio["sample_rate"], audio["channels"]) == ("aac", "48000", 2)
    assert wav_info(batch.event_dir / clip.proxy.audio) == (1, 48000, 2)  # mono, 48 kHz, 16-bit

    only_keyframes = "-select_streams v:0 -skip_frame nokey -show_entries frame=pts_time"
    keyframes = ffprobe_json(video_path, only_keyframes)["frames"]
    times = [float(frame["pts_time"]) for frame in keyframes]
    assert times[:6] == pytest.approx([0, 1, 2, 3, 4, 5], abs=0.034)

    head = video_path.read_bytes()[:200_000]
    assert 0 <= head.find(b"moov") < head.find(b"mdat")  # index first: plays before fully loaded


def test_manifest_records_proxy_details(batch):
    clip = batch.clip("phone.mp4")
    assert (clip.proxy.width, clip.proxy.height, clip.proxy.fps) == (320, 240, 30)
    assert clip.proxy.duration_s == pytest.approx(6.0, abs=0.05)
    assert clip.proxy.settings_key == ProxySettings().key()
    assert clip.proxy.audio_peak_db > -10
    assert clip.source.recorded_at == datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    assert clip.source.video.codec == "h264" and clip.source.audio.codec == "aac"


def test_audio_late_start_is_recorded(batch):
    audio = batch.clip("audio_late.mp4").source.audio
    assert audio.start_time_s == pytest.approx(0.5, abs=0.03)


# --- picture handling


def test_portrait_video_is_turned_upright(batch):
    clip = batch.clip("portrait.mov")
    assert clip.source.video.rotation in (90, 270)
    assert (clip.source.video.display_width, clip.source.video.display_height) == (240, 320)
    assert (clip.proxy.width, clip.proxy.height) == (240, 320)
    video = stream(batch.event_dir / clip.proxy.video, "video")
    assert (video["width"], video["height"]) == (240, 320)
    assert not any("rotation" in d for d in video.get("side_data_list", []))


def test_large_video_is_scaled_down(batch):
    clip = batch.clip("big.mp4")
    assert (clip.proxy.width, clip.proxy.height) == (1280, 720)


def test_hdr_video_is_converted(batch):
    if not (batch.sources / "hdr_hlg.mp4").exists():
        pytest.skip("this FFmpeg has no libx265 to make an HDR test clip")
    clip = batch.clip("hdr_hlg.mp4")
    assert clip.source.video.hdr and clip.source.video.color_transfer == "arib-std-b67"
    assert clip.status is ClipStatus.OK, clip.issues
    video = stream(batch.event_dir / clip.proxy.video, "video")
    assert (video["width"], video["height"]) == (960, 720)
    assert (video["pix_fmt"], video["color_transfer"]) == ("yuv420p", "bt709")


def test_variable_frame_rate_becomes_constant(batch):
    clip = batch.clip("vfr.mp4")
    assert clip.source.video.variable_frame_rate
    video = stream(batch.event_dir / clip.proxy.video, "video")
    assert video["r_frame_rate"] == video["avg_frame_rate"] == "30/1"
    assert clip.proxy.duration_s == pytest.approx(clip.source.duration_s, abs=0.1)


def test_interlaced_anamorphic_video(batch):
    clip = batch.clip("interlaced_anamorphic.mp4")
    assert clip.status is ClipStatus.OK, clip.issues
    assert clip.source.video.interlaced
    video = stream(batch.event_dir / clip.proxy.video, "video")
    assert video.get("sample_aspect_ratio", "1:1") == "1:1"
    assert video["width"] / video["height"] == pytest.approx(16 / 9, abs=0.01)


def test_odd_picture_size_becomes_even(batch):
    clip = batch.clip("odd_size.mp4")
    assert clip.status is ClipStatus.OK, clip.issues
    assert clip.proxy.width % 2 == 0 and clip.proxy.height % 2 == 0


def test_location_and_device_metadata_stripped(batch):
    clip = batch.clip("phone.mp4")
    source_tags = ffprobe_json(batch.sources / "phone.mp4")["format"].get("tags", {})
    assert any("location" in key or "xyz" in key for key in source_tags)  # the test clip has GPS
    proxy_tags = ffprobe_json(batch.event_dir / clip.proxy.video)["format"].get("tags", {})
    assert not any("location" in key or "xyz" in key or "creation" in key for key in proxy_tags)


# --- warnings and rejects


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("no_audio.mp4", "no_audio"),
        ("silent_5_1.mp4", "silent_audio"),
        ("loud.mov", "audio_clipping"),
        ("short.mp4", "short_clip"),
        ("half_download.mp4", "incomplete_file"),
    ],
)
def test_usable_clips_with_warnings(batch, name, code):
    clip = batch.clip(name)
    assert batch.result(name).outcome is Outcome.ADDED
    assert clip.status is ClipStatus.WARNING
    assert code in [issue.code for issue in clip.issues]
    assert (batch.event_dir / clip.proxy.video).is_file()


def test_clip_without_audio_has_no_wav(batch):
    clip = batch.clip("no_audio.mp4")
    assert clip.proxy.audio is None
    assert stream(batch.event_dir / clip.proxy.video, "audio") is None
    assert not (batch.event_dir / "proxies" / f"{clip.clip_id}.wav").exists()


def test_surround_audio_becomes_mono_wav(batch):
    clip = batch.clip("silent_5_1.mp4")
    assert clip.source.audio.channels == 6
    assert wav_info(batch.event_dir / clip.proxy.audio) == (1, 48000, 2)


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("photo.jpg", "still image"),
        ("song.m4a", "audio only"),
        ("notes.txt", "not a video"),
        ("empty.mp4", "empty"),
    ],
)
def test_non_videos_are_skipped_and_not_recorded(batch, name, reason):
    result = batch.result(name)
    assert result.outcome is Outcome.SKIPPED
    assert reason in result.message
    assert all(clip.source.name != name for clip in batch.manifest.clips)


def test_broken_video_is_recorded_as_failed(batch):
    result = batch.result("no_moov.mp4")
    assert result.outcome is Outcome.FAILED
    clip = batch.clip("no_moov.mp4")
    assert clip.status is ClipStatus.FAILED
    assert clip.issues[0].code == "unreadable"
    assert "incomplete or damaged" in clip.issues[0].message
    assert clip.proxy is None and clip.original is None


# --- workspace integrity


def test_manifest_on_disk_is_valid_and_portable(batch):
    raw = json.loads((batch.event_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert raw["schema_version"] == SCHEMA_VERSION
    manifest = load_manifest(batch.event_dir)
    for clip in manifest.clips:
        assert clip.clip_id == clip.source.sha256[:12]
        for rel in filter(
            None, [clip.original, clip.proxy and clip.proxy.video, clip.proxy and clip.proxy.audio]
        ):
            assert "\\" not in rel and not Path(rel).is_absolute()
            assert (batch.event_dir / rel).is_file()


def test_originals_are_untouched_and_read_only(batch):
    for clip in batch.manifest.clips:
        if clip.original is None:
            continue
        source = batch.sources / clip.source.name
        original = batch.event_dir / clip.original
        assert sha256(source) == clip.source.sha256 == sha256(original)
        assert not os.access(original, os.W_OK)


def test_no_leftover_temporary_files(batch):
    leftovers = [
        p.name
        for p in batch.event_dir.rglob("*")
        if ".partial" in p.name or p.name.endswith(".tmp") or p.name == LOCK_NAME
    ]
    assert leftovers == []


# --- running again


def test_running_again_does_no_work(sources, tmp_path, monkeypatch):
    inputs = copy_sources(sources, tmp_path / "in", "phone.mp4", "no_audio.mp4", "notes.txt")
    data = tmp_path / "data"
    first = ingest("again", inputs, data_dir=data)
    assert outcomes(first) == {
        "no_audio.mp4": Outcome.ADDED,
        "notes.txt": Outcome.SKIPPED,
        "phone.mp4": Outcome.ADDED,
    }
    manifest_bytes = (first.manifest_path).read_bytes()

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("no work expected")

    monkeypatch.setattr(media, "make_proxy", must_not_run)
    monkeypatch.setattr("scenefold.ingest._hash_file", must_not_run)
    second = ingest("again", inputs, data_dir=data)
    assert outcomes(second) == {
        "no_audio.mp4": Outcome.UNCHANGED,
        "notes.txt": Outcome.SKIPPED,
        "phone.mp4": Outcome.UNCHANGED,
    }
    assert first.manifest_path.read_bytes() == manifest_bytes


def test_touched_file_is_checked_but_not_rebuilt(sources, tmp_path, monkeypatch):
    inputs = copy_sources(sources, tmp_path / "in", "phone.mp4")
    data = tmp_path / "data"
    ingest("touch", inputs, data_dir=data)
    os.utime(inputs / "phone.mp4", ns=(1_700_000_000 * 10**9, 1_700_000_000 * 10**9))
    monkeypatch.setattr(media, "make_proxy", lambda *a, **k: pytest.fail("rebuilt"))
    report = ingest("touch", inputs, data_dir=data)
    assert outcomes(report) == {"phone.mp4": Outcome.UNCHANGED}
    clip = load_manifest(report.event_dir).clips[0]
    assert clip.source.modified_ns == 1_700_000_000 * 10**9


def test_moved_file_is_recognized(sources, tmp_path):
    data = tmp_path / "data"
    ingest("moved", copy_sources(sources, tmp_path / "a", "phone.mp4"), data_dir=data)
    report = ingest("moved", copy_sources(sources, tmp_path / "b", "phone.mp4"), data_dir=data)
    assert outcomes(report) == {"phone.mp4": Outcome.UNCHANGED}
    clip = load_manifest(report.event_dir).clips[0]
    assert clip.source.path == str((tmp_path / "b" / "phone.mp4").resolve())


def test_deleted_working_copy_is_rebuilt(sources, tmp_path):
    inputs = copy_sources(sources, tmp_path / "in", "phone.mp4")
    data = tmp_path / "data"
    first = ingest("rebuild", inputs, data_dir=data)
    proxy = first.event_dir / first.results[0].clip.proxy.video
    proxy.unlink()
    second = ingest("rebuild", inputs, data_dir=data)
    assert outcomes(second) == {"phone.mp4": Outcome.UPDATED}
    assert proxy.is_file()
    assert len(load_manifest(second.event_dir).clips) == 1


def test_changed_settings_rebuild_working_copies(sources, tmp_path):
    inputs = copy_sources(sources, tmp_path / "in", "big.mp4")
    data = tmp_path / "data"
    ingest("settings", inputs, data_dir=data)
    smaller = ProxySettings(max_short_side=360)
    report = ingest("settings", inputs, data_dir=data, settings=smaller)
    assert outcomes(report) == {"big.mp4": Outcome.UPDATED}
    manifest = load_manifest(report.event_dir)
    assert (manifest.clips[0].proxy.width, manifest.clips[0].proxy.height) == (640, 360)
    assert manifest.proxy_settings == smaller


def test_same_video_under_another_name_is_a_duplicate(sources, tmp_path):
    inputs = copy_sources(sources, tmp_path / "in", "phone.mp4")
    shutil.copy2(inputs / "phone.mp4", inputs / "phone copy.mp4")
    report = ingest("dupes", inputs, data_dir=tmp_path / "data")
    assert outcomes(report) == {"phone copy.mp4": Outcome.ADDED, "phone.mp4": Outcome.DUPLICATE}
    assert "same video as phone copy.mp4" in report.results[1].message
    assert len(load_manifest(report.event_dir).clips) == 1


# --- finding inputs


def test_folders_are_searched_but_junk_and_workspaces_ignored(sources, tmp_path):
    root = tmp_path / "phone_exports"
    copy_sources(sources, root / "day1" / "cam_a", "phone.mp4")
    (root / "._phone.mp4").write_bytes(b"\x00\x05\x16\x07 AppleDouble junk")
    (root / "Thumbs.db").write_bytes(b"junk")
    copy_sources(sources, root / ".hidden", "no_audio.mp4")
    data = root / "data"  # an event workspace inside the folder being ingested
    ingest("inner", copy_sources(sources, tmp_path / "other", "short.mp4"), data_dir=data)

    report = ingest("outer", [root, root / "day1"], data_dir=tmp_path / "data")
    assert outcomes(report) == {"phone.mp4": Outcome.ADDED}  # listed once, workspace files ignored


def test_workspace_files_passed_directly_are_skipped(batch, tmp_path):
    clip = batch.clip("phone.mp4")
    report = ingest("again", [batch.event_dir / clip.proxy.video], data_dir=tmp_path / "data")
    assert report.results[0].outcome is Outcome.SKIPPED
    assert "working file" in report.results[0].message


def test_missing_paths_are_reported_without_creating_an_event(tmp_path):
    report = ingest(
        "ghost", [tmp_path / "nope.mp4", tmp_path / "nofolder"], data_dir=tmp_path / "d"
    )
    assert [r.outcome for r in report.results] == [Outcome.MISSING, Outcome.MISSING]
    assert report.has_failures
    assert not (tmp_path / "d" / "ghost").exists()


def test_nothing_usable_leaves_no_event_folder(sources, tmp_path):
    inputs = copy_sources(sources, tmp_path / "in", "notes.txt", "photo.jpg")
    report = ingest("nothing", inputs, data_dir=tmp_path / "data")
    assert {r.outcome for r in report.results} == {Outcome.SKIPPED}
    assert not report.event_dir.exists()


def test_single_path_argument(sources, tmp_path):
    video = copy_sources(sources, tmp_path / "in", "phone.mp4") / "phone.mp4"
    report = ingest("single", str(video), data_dir=tmp_path / "data")
    assert outcomes(report) == {"phone.mp4": Outcome.ADDED}


def test_progress_is_reported_before_and_after_each_input(sources, tmp_path):
    inputs = copy_sources(sources, tmp_path / "in", "phone.mp4", "notes.txt")
    calls = []
    ingest(
        "progress",
        inputs,
        data_dir=tmp_path / "data",
        progress=lambda n, total, path, result: calls.append((n, total, path.name, result)),
    )
    assert [(n, total, name, r is None) for n, total, name, r in calls] == [
        (1, 2, "notes.txt", True),
        (1, 2, "notes.txt", False),
        (2, 2, "phone.mp4", True),
        (2, 2, "phone.mp4", False),
    ]


# --- failures


def test_invalid_event_name_is_refused(sources, tmp_path):
    with pytest.raises(IngestError, match="invalid event name"):
        ingest("../escape", sources / "phone.mp4", data_dir=tmp_path)


def test_locked_event_is_refused(sources, tmp_path):
    event_dir = tmp_path / "locked"
    event_dir.mkdir()
    (event_dir / LOCK_NAME).write_text("123")
    with pytest.raises(IngestError, match="already running"):
        ingest("locked", sources / "phone.mp4", data_dir=tmp_path)
    assert (event_dir / LOCK_NAME).exists()


@pytest.mark.parametrize(
    ("content", "match"),
    [("{not json", "unreadable"), ('{"schema_version": 99}', "schema_version 99")],
)
def test_unusable_manifest_is_never_overwritten(sources, tmp_path, content, match):
    event_dir = tmp_path / "broken"
    event_dir.mkdir()
    (event_dir / MANIFEST_NAME).write_text(content)
    with pytest.raises(IngestError, match=match):
        ingest("broken", sources / "phone.mp4", data_dir=tmp_path)
    assert (event_dir / MANIFEST_NAME).read_text() == content


def test_one_crash_does_not_stop_the_batch(sources, tmp_path, monkeypatch):
    inputs = copy_sources(sources, tmp_path / "in", "phone.mp4", "short.mp4")
    real_make_proxy, calls = media.make_proxy, []

    def crash_first(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return real_make_proxy(*args, **kwargs)

    monkeypatch.setattr(media, "make_proxy", crash_first)
    report = ingest("crash", inputs, data_dir=tmp_path / "data")
    assert outcomes(report) == {"phone.mp4": Outcome.FAILED, "short.mp4": Outcome.ADDED}
    assert "boom" in report.results[0].message
    assert not list(report.event_dir.rglob("*.partial*"))


def test_unreadable_audio_falls_back_to_picture_only(sources, tmp_path, monkeypatch):
    monkeypatch.setattr(media, "_audio_graph", lambda index, rate: f"[0:{index}]nosuchfilter[wav]")
    inputs = copy_sources(sources, tmp_path / "in", "phone.mp4")
    report = ingest("audiofail", inputs, data_dir=tmp_path / "data")
    clip = report.results[0].clip
    assert clip.status is ClipStatus.WARNING
    assert [issue.code for issue in clip.issues] == ["audio_unreadable"]
    assert clip.proxy.audio is None
    assert stream(report.event_dir / clip.proxy.video, "audio") is None


def test_video_that_cannot_convert_is_recorded_with_its_original(sources, tmp_path, monkeypatch):
    monkeypatch.setattr(
        media,
        "build_proxy_command",
        lambda *a, **k: [media.find_tools().ffmpeg, "-v", "error", "-i", "nope:"],
    )
    inputs = copy_sources(sources, tmp_path / "in", "phone.mp4")
    report = ingest("convertfail", inputs, data_dir=tmp_path / "data")
    assert report.results[0].outcome is Outcome.FAILED
    clip = load_manifest(report.event_dir).clips[0]
    assert clip.status is ClipStatus.FAILED and clip.issues[0].code == "convert_failed"
    assert (report.event_dir / clip.original).is_file()
    assert not (report.event_dir / "proxies" / f"{clip.clip_id}.mp4").exists()


def test_hdr_falls_back_when_conversion_fails(sources, tmp_path, monkeypatch):
    if not (sources / "hdr_hlg.mp4").exists():
        pytest.skip("this FFmpeg has no libx265 to make an HDR test clip")
    real_build = media.build_proxy_command

    def broken_tone_map(*args, **kwargs):
        return [
            part.replace("tonemap=tonemap=", "tonemap=nosuch=")
            for part in real_build(*args, **kwargs)
        ]

    monkeypatch.setattr(media, "build_proxy_command", broken_tone_map)
    inputs = copy_sources(sources, tmp_path / "in", "hdr_hlg.mp4")
    clip = ingest("hdrfail", inputs, data_dir=tmp_path / "data").results[0].clip
    assert clip.status is ClipStatus.WARNING
    assert [issue.code for issue in clip.issues] == ["hdr_not_converted"]


def test_missing_ffmpeg_is_explained(sources, tmp_path, monkeypatch):
    media.find_tools.cache_clear()
    monkeypatch.setattr(media.shutil, "which", lambda name: None)
    try:
        with pytest.raises(IngestError, match="FFmpeg"):
            ingest("noffmpeg", sources / "phone.mp4", data_dir=tmp_path)
    finally:
        monkeypatch.undo()
        media.find_tools.cache_clear()


# --- command line


def test_cli_end_to_end(sources, tmp_path, capsys):
    inputs = copy_sources(sources, tmp_path / "in", "phone.mp4", "short.mp4", "notes.txt")
    data = tmp_path / "data"
    code = main(
        ["ingest", "Match-01", str(inputs), str(tmp_path / "missing.mp4"), "--data-dir", str(data)]
    )
    out = capsys.readouterr().out
    assert code == 1  # the missing path counts as a failure
    assert "phone.mp4 ... added: ok (clip " in out
    assert "short.mp4 ... added: warning" in out
    assert "- only 2.0 s long" in out
    assert "notes.txt ... skipped: not a video file" in out
    assert "missing.mp4 ... missing: file or folder not found" in out
    assert "Summary: 2 added, 1 skipped, 1 missing" in out
    assert (data / "match-01" / MANIFEST_NAME).is_file()

    assert main(["ingest", "match-01", str(inputs), "--data-dir", str(data)]) == 0
    assert "Summary: 2 unchanged, 1 skipped" in capsys.readouterr().out


def test_cli_setup_errors_exit_with_code_2(tmp_path, capsys):
    assert main(["ingest", "bad name", str(tmp_path), "--data-dir", str(tmp_path)]) == 2
    assert "error: invalid event name" in capsys.readouterr().err
