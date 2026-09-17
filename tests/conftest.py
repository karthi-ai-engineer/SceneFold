"""Shared test helpers: tiny generated videos with known timing, and ways to measure outputs.

Every generated clip with a picture shows one white frame, and every clip with sound has a short
click, both at MARK_S seconds of clip time. Measuring where they land in the working copy proves
picture and sound stayed aligned.
"""

import array
import json
import os
import re
import shutil
import subprocess
import wave
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import NoReturn

import pytest

from scenefold.ingest import IngestReport, InputResult, ingest
from scenefold.manifest import Clip, Manifest, load_manifest

# CI sets this so a missing FFmpeg feature fails the run instead of quietly skipping tests.
REQUIRE_FULL_FFMPEG = os.environ.get("SCENEFOLD_REQUIRE_FULL_FFMPEG") == "1"
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(
    not HAVE_FFMPEG and not REQUIRE_FULL_FFMPEG, reason="ffmpeg and ffprobe are not on PATH"
)

MARK_S = 1.0
H264 = "-c:v libx264 -preset ultrafast -pix_fmt yuv420p"

# Test inputs that only some FFmpeg builds can make, and the features they need.
OPTIONAL_SOURCES = {
    "recorder.webm": {"libvpx-vp9", "libopus"},
    "portrait.mov": {"-display_rotation"},  # FFmpeg 6.0+
    "hdr_hlg.mp4": {"libx265"},
}


def _args(parts: tuple[str | Path, ...]) -> list[str]:
    """Strings are split on spaces (options); Paths stay whole (file names may contain spaces)."""
    args: list[str] = []
    for part in parts:
        args += [str(part)] if isinstance(part, Path) else part.split()
    return args


def run_ffmpeg(*parts: str | Path) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", *_args(parts)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )  # fmt: skip
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr)
    return proc


@cache
def ffmpeg_features() -> frozenset[str]:
    """Encoder and filter names of the installed FFmpeg, plus the options tests rely on."""
    names: set[str] = set()
    for flag in ("-encoders", "-filters"):
        names |= {line.split()[1] for line in run_ffmpeg(flag).stdout.splitlines()
                  if len(line.split()) >= 2}  # fmt: skip
    if "-display_rotation" in run_ffmpeg("-h full").stdout:
        names.add("-display_rotation")
    return frozenset(names)


def unavailable(reason: str) -> NoReturn:
    """Skip a test this machine can't run; under SCENEFOLD_REQUIRE_FULL_FFMPEG=1, fail it."""
    if REQUIRE_FULL_FFMPEG:
        pytest.fail(f"{reason} (SCENEFOLD_REQUIRE_FULL_FFMPEG=1 does not allow skipping)")
    pytest.skip(reason)


def require_ffmpeg(features: Iterable[str], purpose: str) -> None:
    if missing := sorted(set(features) - ffmpeg_features()):
        unavailable(f"{purpose} needs an FFmpeg with {', '.join(missing)}")


def require_source(name: str) -> None:
    require_ffmpeg(OPTIONAL_SOURCES.get(name, ()), f"the test input {name}")


def flash_video(seconds: float, color: str, flash_at: float = MARK_S) -> str:
    white = f"enable='gte(t,{flash_at})*lt(t,{flash_at + 0.03})'"
    return f"color={color}:s=320x240:r=30:d={seconds},drawbox=w=iw:h=ih:c=white:t=fill:{white}"


def click_audio(seconds: float, click_at: float = MARK_S) -> str:
    click = f"if(gte(t,{click_at})*lt(t,{click_at + 0.02}),0.8*sin(2*PI*1000*t),0)"
    return f"aevalsrc='{click}':s=48000:d={seconds}"


def av(seconds: float, color: str) -> str:
    return f"-f lavfi -i {flash_video(seconds, color)} -f lavfi -i {click_audio(seconds)}"


def truncate(src: Path, dest: Path, fraction: float) -> None:
    data = src.read_bytes()
    dest.write_bytes(data[: int(len(data) * fraction)])
    src.unlink()


# fmt: off
def make_sources(folder: Path) -> None:
    """Generate the test inputs. Each video has different content so none are duplicates."""
    tone = f"-f lavfi -i {click_audio(6)}"
    aac = "-c:a aac"
    can_make = {name: needs <= ffmpeg_features() for name, needs in OPTIONAL_SOURCES.items()}

    run_ffmpeg(av(6, "black"), H264, aac, "-metadata location=+35.6895+139.6917/",
               "-metadata creation_time=2026-09-01T10:00:00Z", folder / "phone.mp4")
    # audio track starts 0.5 s after the picture; the click still happens at file time 1.0
    run_ffmpeg(f"-f lavfi -i {flash_video(6, 'navy')}",
               f"-itsoffset 0.5 -f lavfi -i {click_audio(5.5, click_at=0.5)}",
               H264, aac, folder / "audio_late.mp4")
    # picture starts 0.5 s after the audio; the flash still happens at file time 1.0
    run_ffmpeg(f"-itsoffset 0.5 -f lavfi -i {flash_video(5.5, 'maroon', flash_at=0.5)}",
               f"-f lavfi -i {click_audio(6)}", H264, aac, folder / "video_late.mp4")
    run_ffmpeg(av(6, "darkgreen"), H264, aac, folder / "clip.mkv")
    if can_make["recorder.webm"]:  # a browser recording: VP9/Opus WebM with no duration
        run_ffmpeg(av(6, "gray"), "-c:v libvpx-vp9 -b:v 200k -deadline realtime",
                   "-c:a libopus -live 1", folder / "recorder.webm")
    run_ffmpeg(av(6, "darkred"), H264, aac, folder / "日本語 動画.mp4")
    run_ffmpeg(av(6, "darkslategray"), H264, aac, folder / "-dash-name.mp4")
    if can_make["portrait.mov"]:
        run_ffmpeg(av(6, "purple"), H264, aac, folder / "upright.mp4")
        run_ffmpeg("-display_rotation:v:0 90 -i", folder / "upright.mp4", "-c copy",
                   folder / "portrait.mov")
        (folder / "upright.mp4").unlink()

    run_ffmpeg("-f lavfi -i testsrc2=s=1920x1080:r=30:d=6", tone, H264, aac, folder / "big.mp4")
    run_ffmpeg("-f lavfi -i testsrc=s=320x240:r=30:d=6", tone,
               r"-vf select='lt(n\,30)+not(mod(n\,3))' -fps_mode vfr", H264, aac,
               folder / "vfr.mp4")
    run_ffmpeg("-f lavfi -i testsrc=s=720x480:r=30000/1001:d=6", tone, "-vf setsar=32/27",
               H264, "-flags +ildct+ilme -x264-params tff=1", aac,
               folder / "interlaced_anamorphic.mp4")
    run_ffmpeg("-f lavfi -i testsrc=s=321x241:r=30:d=6", tone,
               "-c:v libx264 -preset ultrafast -pix_fmt yuv444p", aac, folder / "odd_size.mp4")
    if can_make["hdr_hlg.mp4"]:
        run_ffmpeg("-f lavfi -i testsrc2=s=1280x960:r=30:d=6", tone,
                   "-vf format=yuv420p10le -c:v libx265 -preset ultrafast -tag:v hvc1",
                   "-x265-params log-level=error:colorprim=bt2020:transfer=arib-std-b67"
                   ":colormatrix=bt2020nc",
                   "-color_primaries bt2020 -color_trc arib-std-b67 -colorspace bt2020nc",
                   aac, folder / "hdr_hlg.mp4")

    run_ffmpeg(f"-f lavfi -i {flash_video(6, 'olive')}", H264, folder / "no_audio.mp4")
    run_ffmpeg(f"-f lavfi -i {flash_video(6, 'teal')}", "-f lavfi -i anullsrc=r=44100:cl=5.1",
               "-t 6", H264, aac, folder / "silent_5_1.mp4")
    run_ffmpeg(f"-f lavfi -i {flash_video(6, 'brown')}", "-f lavfi -i sine=f=440:d=6,volume=12",
               H264, "-c:a pcm_s16le", folder / "loud.mov")
    run_ffmpeg(av(2, "indigo"), H264, aac, folder / "short.mp4")

    run_ffmpeg("-f lavfi -i testsrc2=s=320x240:r=30:d=10 -f lavfi -i sine=f=330:d=10",
               H264, aac, "-movflags +faststart", folder / "full_download.mp4")
    truncate(folder / "full_download.mp4", folder / "half_download.mp4", 0.5)
    run_ffmpeg(av(6, "darkblue"), H264, aac, folder / "recording.mp4")
    truncate(folder / "recording.mp4", folder / "no_moov.mp4", 0.5)  # its index was at the end

    run_ffmpeg("-f lavfi -i testsrc=s=320x240:d=1 -frames:v 1", folder / "photo.jpg")
    run_ffmpeg(f"-f lavfi -i {click_audio(6)}", aac, folder / "song.m4a")
    (folder / "notes.txt").write_text("not a video", encoding="utf-8")
    (folder / "empty.mp4").write_bytes(b"")
# fmt: on


@pytest.fixture(scope="session")
def sources(tmp_path_factory) -> Path:
    if not HAVE_FFMPEG:
        unavailable("ffmpeg and ffprobe are not on PATH")
    folder = tmp_path_factory.mktemp("sources")
    make_sources(folder)
    return folder


@dataclass
class Batch:
    report: IngestReport
    event_dir: Path
    manifest: Manifest
    sources: Path

    def result(self, name: str) -> InputResult:
        return next(r for r in self.report.results if r.path.name == name)

    def clip(self, name: str) -> Clip:
        return next(c for c in self.manifest.clips if c.source.name == name)


@pytest.fixture(scope="session")
def batch(sources, tmp_path_factory) -> Batch:
    """All generated sources ingested once, as one folder."""
    data_dir = tmp_path_factory.mktemp("data")
    report = ingest("batch", [sources], data_dir=data_dir)
    manifest = load_manifest(report.event_dir)
    assert manifest is not None
    return Batch(report, report.event_dir, manifest, sources)


def ffprobe_json(path: Path, extra: str = "") -> dict:
    cmd = ["ffprobe", "-v", "error", "-of", "json", "-show_format", "-show_streams"]
    proc = subprocess.run(
        [*cmd, *extra.split(), str(path)], capture_output=True, text=True, encoding="utf-8"
    )
    return json.loads(proc.stdout)


def stream(path: Path, kind: str) -> dict | None:
    return next((s for s in ffprobe_json(path)["streams"] if s["codec_type"] == kind), None)


def flash_time(video: Path) -> float:
    """Presentation time of the brightest frame."""
    stats = "-vf signalstats,metadata=mode=print:key=lavfi.signalstats.YAVG -f null -"
    stderr = run_ffmpeg("-i", video, stats).stderr
    best_time, best_luma, current = None, -1.0, None
    for line in stderr.splitlines():
        if match := re.search(r"pts_time:([-\d.]+)", line):
            current = float(match.group(1))
        if (match := re.search(r"YAVG=([\d.]+)", line)) and float(match.group(1)) > best_luma:
            best_time, best_luma = current, float(match.group(1))
    assert best_time is not None, stderr[-500:]
    return best_time


def wav_info(path: Path) -> tuple[int, int, int]:
    """(channels, sample rate, bytes per sample)"""
    with wave.open(str(path)) as wav:
        return wav.getnchannels(), wav.getframerate(), wav.getsampwidth()


def click_time(wav_path: Path) -> float:
    """Time of the first sample reaching 30% of the loudest one."""
    with wave.open(str(wav_path)) as wav:
        rate, channels = wav.getframerate(), wav.getnchannels()
        samples = array.array("h", wav.readframes(wav.getnframes()))[::channels]
    threshold = 0.3 * max(abs(s) for s in samples)
    return next(i for i, s in enumerate(samples) if abs(s) >= threshold) / rate


def decode_audio(video: Path, dest: Path) -> Path:
    run_ffmpeg("-v error -i", video, "-map 0:a:0 -ac 1 -c:a pcm_s16le", dest)
    return dest
