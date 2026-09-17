"""FFmpeg wrappers: read a file's details (ffprobe) and make its working copy (ffmpeg)."""

import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from fractions import Fraction
from functools import cache
from pathlib import Path

from scenefold.manifest import AudioStream, Issue, ProxySettings, VideoStream

HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}  # PQ and HLG
INTERLACED_FIELD_ORDERS = {"tt", "bb", "tb", "bt"}
IMAGE_CONTAINERS = {"image2", "image2pipe", "gif", "apng", "webp", "ico", "bmp", "tiff"}
REQUIRED_ENCODERS = {"libx264", "aac", "pcm_s16le"}
REQUIRED_FILTERS = {"scale", "setsar", "fps", "format", "aresample", "asplit", "aformat"}
REQUIRED_FILTERS |= {"volumedetect"}
TONE_MAP_FILTERS = {"zscale", "tonemap"}
SILENCE_FLOOR_DB = -120.0

_LOG_PREFIX = re.compile(r"^\[[^\]]*@ [0-9a-fA-Fx]+\]\s*")
_ERROR_HINT = re.compile(
    r"error|invalid|not found|failed|no such|unsupported|could not|cannot|unknown|incomplete|"
    r"end of file|partial file",
    re.IGNORECASE,
)
_ERROR_NOISE = re.compile(
    r"task finished with error|terminating thread|error number", re.IGNORECASE
)


class MediaToolsError(RuntimeError):
    """FFmpeg is missing or lacks a feature Scenefold needs."""


class ProbeError(RuntimeError):
    """ffprobe could not read the file."""


class ProxyError(RuntimeError):
    """ffmpeg could not make a working copy."""


@dataclass(frozen=True)
class Tools:
    ffmpeg: str
    ffprobe: str
    encoders: frozenset[str]
    filters: frozenset[str]

    @property
    def can_tone_map(self) -> bool:
        return self.filters >= TONE_MAP_FILTERS

    @property
    def can_deinterlace(self) -> bool:
        return "bwdif" in self.filters


@cache
def find_tools() -> Tools:
    """Locate ffmpeg/ffprobe and check they can do what ingest needs."""
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise MediaToolsError(
            "FFmpeg (ffmpeg and ffprobe) was not found on PATH. Install it "
            "(Windows: winget install Gyan.FFmpeg, macOS: brew install ffmpeg, "
            "Linux: sudo apt install ffmpeg), then open a new terminal."
        )
    tools = Tools(
        ffmpeg, ffprobe, _list_names(ffmpeg, "-encoders"), _list_names(ffmpeg, "-filters")
    )
    missing = (REQUIRED_ENCODERS - tools.encoders) | (REQUIRED_FILTERS - tools.filters)
    if missing:
        raise MediaToolsError(
            f"this FFmpeg build lacks {', '.join(sorted(missing))}; install a full build "
            "(for example Gyan.FFmpeg on Windows or Homebrew ffmpeg on macOS)"
        )
    return tools


def _list_names(ffmpeg: str, flag: str) -> frozenset[str]:
    proc = _run([ffmpeg, "-hide_banner", flag])
    names = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            names.add(parts[1])
    return frozenset(names)


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def error_summary(stderr: str, path: Path | None = None) -> str:
    """Pick the few lines of FFmpeg output that explain what went wrong."""
    lines: list[str] = []
    for raw in stderr.splitlines():
        line = _LOG_PREFIX.sub("", raw.strip())
        if path is not None:
            line = line.replace(f"{path}: ", "")
        if (
            line
            and _ERROR_HINT.search(line)
            and not _ERROR_NOISE.search(line)
            and line not in lines
        ):
            lines.append(line)
    if not lines:
        tail = [line.strip() for line in stderr.splitlines() if line.strip()]
        lines = tail[-1:] or ["unknown FFmpeg error"]
    return "; ".join(lines[-2:])[:300]


# --- probing


@dataclass
class MediaInfo:
    container: str | None
    duration_s: float | None
    recorded_at: datetime | None
    video: VideoStream | None
    audio: AudioStream | None
    is_image: bool
    incomplete: bool  # ffprobe noticed the file is cut short


def probe(path: Path) -> MediaInfo:
    proc, data = _ffprobe(path)
    if proc.returncode != 0:
        raise ProbeError(error_summary(proc.stderr, path))
    return parse_probe(data, proc.stderr)


def _ffprobe(path: Path) -> tuple[subprocess.CompletedProcess[str], dict]:
    options = "-v error -print_format json -show_format -show_streams".split()
    proc = _run([find_tools().ffprobe, *options, str(path)])
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        data = {}
    return proc, data


def parse_probe(data: dict, stderr: str = "") -> MediaInfo:
    """Turn ffprobe's JSON into the facts ingest needs (pure; unit-testable)."""
    fmt = data.get("format") or {}
    streams = data.get("streams") or []
    container = fmt.get("format_name")

    videos = [
        s
        for s in streams
        if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")
    ]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    video_raw = _pick(videos)
    audio_raw = _pick(audios)
    video = _video_stream(video_raw) if video_raw else None

    is_image = bool(video_raw) and (
        container in IMAGE_CONTAINERS
        or (container or "").endswith("_pipe")
        or str(video_raw.get("nb_frames")) == "1"
    )
    return MediaInfo(
        container=container,
        duration_s=_duration(fmt, video_raw, audio_raw),
        recorded_at=_recorded_at(fmt, video_raw),
        video=video,
        audio=_audio_stream(audio_raw) if audio_raw else None,
        is_image=is_image,
        incomplete="partial file" in stderr.lower(),
    )


def _pick(streams: list[dict]) -> dict | None:
    for stream in streams:
        if (stream.get("disposition") or {}).get("default"):
            return stream
    return streams[0] if streams else None


def _video_stream(s: dict) -> VideoStream:
    width, height = int(s.get("width") or 0), int(s.get("height") or 0)
    rotation = _rotation(s)
    pixel_aspect = _ratio(s.get("sample_aspect_ratio")) or Fraction(1)
    display_w, display_h = round(width * pixel_aspect), height
    if rotation in (90, 270):
        display_w, display_h = display_h, display_w
    r_rate, avg_rate = _ratio(s.get("r_frame_rate")), _ratio(s.get("avg_frame_rate"))
    fps = avg_rate or r_rate
    transfer = s.get("color_transfer")
    return VideoStream(
        index=int(s["index"]),
        codec=s.get("codec_name") or "unknown",
        width=width,
        height=height,
        display_width=display_w,
        display_height=display_h,
        rotation=rotation,
        fps=round(float(fps), 3) if fps else None,
        variable_frame_rate=bool(r_rate and avg_rate and abs(r_rate - avg_rate) / r_rate > 0.01),
        pixel_format=s.get("pix_fmt"),
        color_transfer=transfer,
        hdr=transfer in HDR_TRANSFERS,
        interlaced=s.get("field_order") in INTERLACED_FIELD_ORDERS,
        start_time_s=_float(s.get("start_time")),
    )


def _audio_stream(s: dict) -> AudioStream:
    return AudioStream(
        index=int(s["index"]),
        codec=s.get("codec_name") or "unknown",
        sample_rate=_int(s.get("sample_rate")),
        channels=_int(s.get("channels")),
        start_time_s=_float(s.get("start_time")),
    )


def _rotation(s: dict) -> int:
    """Clockwise degrees to display upright, rounded to a quarter turn."""
    degrees = None
    for side_data in s.get("side_data_list") or []:
        if "rotation" in side_data:
            # ffprobe reports the display matrix angle counter-clockwise
            degrees = -float(side_data["rotation"])
            break
    if degrees is None and (s.get("tags") or {}).get("rotate") is not None:
        degrees = _float(s["tags"]["rotate"])  # older files: clockwise
    if degrees is None:
        return 0
    return int(round(degrees / 90)) * 90 % 360


def _duration(fmt: dict, *streams: dict | None) -> float | None:
    candidates = [fmt.get("duration")]
    for s in streams:
        if s:
            # Matroska/WebM keep per-stream duration only as a "HH:MM:SS.fff" tag
            candidates += [s.get("duration"), (s.get("tags") or {}).get("DURATION")]
    for value in candidates:
        if value is None:
            continue
        text = str(value)
        seconds = _hms(text) if ":" in text else _float(text)
        if seconds is not None and 0 < seconds < math.inf:
            return round(seconds, 3)
    return None


def _recorded_at(fmt: dict, video: dict | None) -> datetime | None:
    tag_sets = [fmt.get("tags") or {}, (video or {}).get("tags") or {}]
    keys = ["com.apple.quicktime.creationdate", "creation_time", "date"]
    for key in keys:
        for tags in tag_sets:
            value = next((v for k, v in tags.items() if k.lower() == key), None)
            if value and (parsed := _parse_datetime(str(value))):
                return parsed
    return None


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)  # FFmpeg writes creation_time in UTC
    return parsed if parsed.year >= 1971 else None  # 1904/1970 mean "never set"


def _ratio(value) -> Fraction | None:
    try:
        text = str(value).replace(":", "/")
        ratio = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None
    return ratio if ratio > 0 else None


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value) -> int | None:
    number = _float(value)
    return int(number) if number is not None else None


def _hms(value: str) -> float | None:
    try:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None


# --- working copies


@dataclass
class ProxyResult:
    width: int
    height: int
    duration_s: float
    has_audio: bool
    audio_mean_db: float | None = None
    audio_peak_db: float | None = None
    audio_clipped_fraction: float | None = None
    issues: list[Issue] = field(default_factory=list)


def proxy_size(video: VideoStream, max_short_side: int) -> tuple[int, int]:
    """Picture size of the working copy: shorter side capped, aspect kept, both sides even."""
    width, height = video.display_width, video.display_height
    scale = min(1.0, max_short_side / min(width, height))
    return _even(width * scale), _even(height * scale)


def _even(value: float) -> int:
    return max(2, int(value / 2 + 0.5) * 2)


def make_proxy(
    src: Path, info: MediaInfo, settings: ProxySettings, video_out: Path, audio_out: Path
) -> ProxyResult:
    """Make the working video (and mono WAV) for one clip.

    Falls back step by step so one problem doesn't lose the whole clip: first without HDR tone
    mapping, then without audio. Each fallback is reported as an issue.
    """
    tools = find_tools()
    video = info.video
    if video is None or min(video.display_width, video.display_height) <= 0:
        raise ProxyError("the video track has no picture size")

    issues: list[Issue] = []
    wants_tone_map = video.hdr and tools.can_tone_map
    if video.hdr and not tools.can_tone_map:
        issues.append(_hdr_issue("this FFmpeg build cannot convert HDR"))
    attempts = [
        (tone_map, with_audio)
        for with_audio in ([True, False] if info.audio else [False])
        for tone_map in ([True, False] if wants_tone_map else [False])
    ]

    first_error = None
    for tone_map, with_audio in attempts:
        for path in (video_out, audio_out):
            path.unlink(missing_ok=True)
        cmd = build_proxy_command(src, info, settings, video_out, audio_out, tone_map, with_audio)
        proc = _run(cmd)
        if proc.returncode == 0 and video_out.is_file() and video_out.stat().st_size > 0:
            break
        first_error = first_error or error_summary(proc.stderr)
    else:
        for path in (video_out, audio_out):
            path.unlink(missing_ok=True)
        raise ProxyError(first_error or "ffmpeg failed")

    if wants_tone_map and not tone_map:
        issues.append(_hdr_issue("HDR conversion failed"))
    if info.audio and not with_audio:
        issues.append(
            Issue(
                code="audio_unreadable",
                message="the audio track could not be decoded; this clip can't be synced by sound",
            )
        )

    result = _read_proxy(video_out, expect_audio=with_audio)
    result.issues = issues
    if with_audio:
        _read_audio_levels(proc.stderr, result)
    return result


def build_proxy_command(
    src: Path,
    info: MediaInfo,
    settings: ProxySettings,
    video_out: Path,
    audio_out: Path,
    tone_map: bool,
    with_audio: bool,
) -> list[str]:
    tools = find_tools()
    video = info.video
    assert video is not None
    width, height = proxy_size(video, settings.max_short_side)

    chain = []
    if video.interlaced and tools.can_deinterlace:
        chain.append("bwdif=mode=send_frame")
    if tone_map:
        chain += [
            f"zscale=w={width}:h={height}:tin={video.color_transfer}:pin=bt2020:min=bt2020nc"
            ":t=linear:npl=100",
            "format=gbrpf32le",
            "zscale=p=bt709",
            "tonemap=tonemap=hable:desat=0",
            "zscale=t=bt709:m=bt709:r=tv",
        ]
    else:
        chain.append(f"scale={width}:{height}")
    # fps start_time=0 pads (or trims) the picture so it starts exactly at clip time 0
    chain += ["setsar=1", f"fps={settings.fps}:start_time=0", "format=yuv420p"]
    graph = f"[0:{video.index}]{','.join(chain)}[v]"
    if with_audio:
        assert info.audio is not None
        graph += ";" + _audio_graph(info.audio.index, settings.audio_sample_rate)

    keyframes = max(1, round(settings.fps * settings.keyframe_interval_s))
    cmd = [tools.ffmpeg, *"-hide_banner -nostdin -nostats -v info -y -i".split(), str(src)]
    cmd += ["-filter_complex", graph, "-map", "[v]"] + (["-map", "[pa]"] if with_audio else [])
    cmd += (
        f"-c:v libx264 -preset {settings.preset} -crf {settings.crf} -pix_fmt yuv420p "
        f"-g {keyframes} -keyint_min {keyframes} -sc_threshold 0"
    ).split()
    if tone_map:
        cmd += "-color_primaries bt709 -color_trc bt709 -colorspace bt709".split()
    if with_audio:
        cmd += f"-c:a aac -b:a {settings.audio_bitrate_kbps}k -ac 2".split()
    # strip metadata (GPS location, device names) from working copies
    cmd += "-map_metadata -1 -map_chapters -1 -movflags +faststart".split() + [str(video_out)]
    if with_audio:
        cmd += "-map [wav] -c:a pcm_s16le -rf64 auto -map_metadata -1".split() + [str(audio_out)]
    return cmd


def _audio_graph(stream_index: int, sample_rate: int) -> str:
    # first_pts=0 pads (or trims) the sound so it starts exactly at clip time 0, like the picture
    return (
        f"[0:{stream_index}]aresample={sample_rate}:async=1:first_pts=0,asplit=2[pa][wa];"
        "[wa]aformat=channel_layouts=mono,volumedetect[wav]"
    )


def _hdr_issue(reason: str) -> Issue:
    return Issue(code="hdr_not_converted", message=f"{reason}; colors may look washed out")


def _read_proxy(path: Path, expect_audio: bool) -> ProxyResult:
    proc, data = _ffprobe(path)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = _float((data.get("format") or {}).get("duration"))
    if proc.returncode != 0 or video is None or not duration:
        raise ProxyError("the working copy came out empty or unreadable")
    if expect_audio and not has_audio:
        raise ProxyError("the working copy is missing its audio track")
    return ProxyResult(
        width=int(video["width"]),
        height=int(video["height"]),
        duration_s=round(duration, 3),
        has_audio=has_audio,
    )


def _read_audio_levels(stderr: str, result: ProxyResult) -> None:
    """Parse the volumedetect report printed for the WAV output."""

    def last(pattern: str) -> str | None:
        matches = re.findall(pattern, stderr)
        return matches[-1] if matches else None

    def decibels(value: str | None) -> float | None:
        number = _float(value)
        if number is None:
            return None
        return max(number, SILENCE_FLOOR_DB)  # "-inf" (digital silence) becomes the floor

    mean, peak = last(r"mean_volume: (-?[\d.]+|-?inf) dB"), last(r"max_volume: (-?[\d.]+|-?inf) dB")
    samples, full_scale = last(r"n_samples: (\d+)"), last(r"histogram_0db: (\d+)")
    result.audio_mean_db = decibels(mean)
    result.audio_peak_db = decibels(peak)
    if samples and int(samples) > 0:
        result.audio_clipped_fraction = int(full_scale or 0) / int(samples)
